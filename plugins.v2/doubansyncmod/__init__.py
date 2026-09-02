import datetime
from pathlib import Path
from threading import Lock
from typing import Optional, Any, List, Dict, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.media import MediaChain
from app.db.subscribe_oper import SubscribeOper
from app.db.user_oper import UserOper
from app.schemas.types import MediaType, EventType, SystemConfigKey

from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.chain.subscribe import SubscribeChain
from app.core.config import settings
from app.core.event import Event
from app.core.event import eventmanager
from app.core.metainfo import MetaInfo
from app.helper.rss import RssHelper
from app.utils.http import RequestUtils
from app.log import logger
from app.plugins import _PluginBase

lock = Lock()


class DoubanSyncMod(_PluginBase):
    # 插件名称
    plugin_name = "豆瓣想看魔改版"
    # 插件描述
    plugin_desc = "同步豆瓣想看数据，按条件添加 MoviePilot 订阅或 Seerr 请求。"
    # 插件图标
    plugin_icon = "douban.png"
    # 插件版本
    plugin_version = "2.2.0"
    # 插件作者
    plugin_author = "jxxghp,dwhmofly,weizsw"
    # 作者主页
    author_url = "https://github.com/weizsw"
    # 插件配置项ID前缀
    plugin_config_prefix = "doubansyncmod_"
    # 加载顺序
    plugin_order = 3
    # 可使用的用户级别
    auth_level = 2

    # 私有变量
    _interests_url: str = "https://www.douban.com/feed/people/%s/interests"
    _scheduler: Optional[BackgroundScheduler] = None
    _cache_path: Optional[Path] = None

    # 配置属性
    _enabled: bool = False
    _onlyonce: bool = False
    _cron: str = ""
    _notify: bool = False
    _days: int = 7
    _users: str = ""
    _clear: bool = False
    _clearflag: bool = False
    _search_download = False
    _seerr_host: str = ""
    _seerr_api_key: str = ""
    _seerr_movie: bool = False
    _seerr_tv: bool = False
    _seerr_genre_ids: List[int] = []
    _seerr_languages: List[str] = []
    _seerr_year_from: Optional[int] = None
    _seerr_year_to: Optional[int] = None

    _genre_options = [
        {"title": title, "value": value} for title, value in [
            ("动作", 28), ("冒险", 12), ("动画", 16), ("喜剧", 35), ("犯罪", 80),
            ("纪录", 99), ("剧情", 18), ("家庭", 10751), ("奇幻", 14), ("历史", 36),
            ("恐怖", 27), ("音乐", 10402), ("悬疑", 9648), ("爱情", 10749), ("科幻", 878),
            ("电视电影", 10770), ("惊悚", 53), ("战争", 10752), ("西部", 37),
            ("动作冒险（剧集）", 10759), ("儿童", 10762), ("新闻", 10763), ("真人秀", 10764),
            ("科幻奇幻（剧集）", 10765), ("肥皂剧", 10766), ("脱口秀", 10767),
            ("战争政治（剧集）", 10768)
        ]
    ]
    _language_options = [
        {"title": title, "value": value} for title, value in [
            ("中文", "zh"), ("英语", "en"), ("日语", "ja"), ("韩语", "ko"),
            ("法语", "fr"), ("德语", "de"), ("西班牙语", "es"), ("意大利语", "it"),
            ("葡萄牙语", "pt"), ("俄语", "ru"), ("印地语", "hi"), ("泰语", "th")
        ]
    ]

    def init_plugin(self, config: dict = None):

        # 停止现有任务
        self.stop_service()

        # 配置
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._notify = config.get("notify")
            self._days = config.get("days")
            self._users = config.get("users")
            self._onlyonce = config.get("onlyonce")
            self._clear = config.get("clear")
            self._search_download = config.get("search_download")
            host = str(config.get("seerr_host") or "").strip().rstrip("/")
            if host.endswith("/api/v1"):
                host = host[:-7]
            if host and not host.startswith(("http://", "https://")):
                host = f"http://{host}"
            self._seerr_host = host
            self._seerr_api_key = str(config.get("seerr_api_key") or "").strip()
            self._seerr_movie = bool(config.get("seerr_movie"))
            self._seerr_tv = bool(config.get("seerr_tv"))
            try:
                self._seerr_genre_ids = [int(value) for value in config.get("seerr_genre_ids") or []]
            except (TypeError, ValueError):
                self._seerr_genre_ids = []
            languages = config.get("seerr_languages") or []
            self._seerr_languages = [str(value).strip().lower() for value in languages] \
                if isinstance(languages, list) else []
            try:
                self._seerr_year_from = int(str(config.get("seerr_year_from"))) \
                    if config.get("seerr_year_from") not in (None, "") else None
                self._seerr_year_to = int(str(config.get("seerr_year_to"))) \
                    if config.get("seerr_year_to") not in (None, "") else None
            except (TypeError, ValueError):
                self._seerr_year_from = self._seerr_year_to = None

        if self._enabled or self._onlyonce:
            if self._onlyonce:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info(f"豆瓣想看服务启动，立即运行一次")
                self._scheduler.add_job(func=self.sync, trigger='date',
                                        run_date=datetime.datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                        )

                # 启动任务
                if self._scheduler.get_jobs():
                    self._scheduler.print_jobs()
                    self._scheduler.start()

            if self._onlyonce or self._clear:
                # 关闭一次性开关
                self._onlyonce = False
                # 记录缓存清理标志
                self._clearflag = self._clear
                # 关闭清理缓存
                self._clear = False
                # 保存配置
                self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        return [{
            "cmd": "/douban_sync",
            "event": EventType.PluginAction,
            "desc": "同步豆瓣想看",
            "category": "订阅",
            "data": {
                "action": "douban_sync"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        return [
            {
                "path": "/delete_history",
                "endpoint": self.delete_history,
                "methods": ["GET"],
                "summary": "删除豆瓣同步历史记录"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            return [
                {
                    "id": "DoubanSyncMod",
                    "name": "豆瓣想看同步服务",
                    "trigger": CronTrigger.from_crontab(self._cron),
                    "func": self.sync,
                    "kwargs": {}
                }
            ]
        elif self._enabled:
            return [
                {
                    "id": "DoubanSyncMod",
                    "name": "豆瓣想看同步服务",
                    "trigger": "interval",
                    "func": self.sync,
                    "kwargs": {"minutes": 30}
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式，留空自动'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'days',
                                            'label': '同步天数'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'users',
                                            'label': '用户列表',
                                            'placeholder': '豆瓣用户ID，多个用英文逗号分隔'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'clear',
                                            'label': '清理历史记录',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4,
                                    'style': 'display:flex;align-items: center;'
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'search_download',
                                            'label': '搜索下载',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'seerr_host',
                                        'label': 'Seerr 地址',
                                        'placeholder': 'http://seerr:5055'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'seerr_api_key',
                                        'label': 'Seerr API Key',
                                        'type': 'password'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 2},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'seerr_movie', 'label': '电影转 Seerr'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 2},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {'model': 'seerr_tv', 'label': '电视剧转 Seerr'}
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 8},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'seerr_genre_ids',
                                        'label': '不转发的类型',
                                        'items': self._genre_options,
                                        'multiple': True,
                                        'chips': True,
                                        'clearable': True
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VCombobox',
                                    'props': {
                                        'model': 'seerr_languages',
                                        'label': '允许的原始语言',
                                        'items': self._language_options,
                                        'multiple': True,
                                        'chips': True,
                                        'clearable': True,
                                        'hint': '留空不限；可输入 ISO 639-1 语言代码',
                                        'persistent-hint': True
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'seerr_year_from',
                                        'label': '最早发行年份',
                                        'type': 'number'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'seerr_year_to',
                                        'label': '最晚发行年份',
                                        'type': 'number'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': 'Seerr 条件按媒体类型、排除类型、允许语言和年份范围同时匹配；留空的条件不限。'
                                                    '匹配项会在搜索下载前转交 Seerr，并使用 Seerr 默认质量配置；失败不会回退到 MoviePilot。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cron": "*/30 * * * *",
            "days": 7,
            "users": "",
            "clear": False,
            "search_download": False,
            "seerr_host": "",
            "seerr_api_key": "",
            "seerr_movie": False,
            "seerr_tv": False,
            "seerr_genre_ids": [],
            "seerr_languages": [],
            "seerr_year_from": None,
            "seerr_year_to": None
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        # 查询同步详情
        historys = self.get_data('history')
        if not historys:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]
        # 数据按时间降序排序
        historys = sorted(historys, key=lambda x: x.get('time'), reverse=True)
        # 拼装页面
        contents = []
        for history in historys:
            title = history.get("title")
            poster = history.get("poster")
            mtype = history.get("type")
            time_str = history.get("time")
            doubanid = history.get("doubanid")
            action = {"download": "下载", "subscribe": "订阅", "exist": "存在", "seerr": "Seerr"}.get(
                history.get("action"), history.get("action"))
            contents.append(
                {
                    'component': 'VCard',
                    'content': [
                        {
                            "component": "VDialogCloseBtn",
                            "props": {
                                'innerClass': 'absolute top-0 right-0',
                            },
                            'events': {
                                'click': {
                                    'api': 'plugin/DoubanSyncMod/delete_history',
                                    'method': 'get',
                                    'params': {
                                        'doubanid': doubanid,
                                        'apikey': settings.API_TOKEN
                                    }
                                }
                            },
                        },
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-start flex-nowrap flex-row',
                            },
                            'content': [
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VImg',
                                            'props': {
                                                'src': poster,
                                                'height': 120,
                                                'width': 80,
                                                'aspect-ratio': '2/3',
                                                'class': 'object-cover shadow ring-gray-500',
                                                'cover': True
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VCardTitle',
                                            'props': {
                                                'class': 'ps-1 pe-5 break-words whitespace-break-spaces'
                                            },
                                            'content': [
                                                {
                                                    'component': 'a',
                                                    'props': {
                                                        'href': f"https://movie.douban.com/subject/{doubanid}",
                                                        'target': '_blank'
                                                    },
                                                    'text': title
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'类型：{mtype}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'时间：{time_str}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'操作：{action}'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )

        return [
            {
                'component': 'div',
                'props': {
                    'class': 'grid gap-3 grid-info-card',
                },
                'content': contents
            }
        ]

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "days": self._days,
            "users": self._users,
            "clear": self._clear,
            "search_download": self._search_download,
            "seerr_host": self._seerr_host,
            "seerr_api_key": self._seerr_api_key,
            "seerr_movie": self._seerr_movie,
            "seerr_tv": self._seerr_tv,
            "seerr_genre_ids": self._seerr_genre_ids,
            "seerr_languages": self._seerr_languages,
            "seerr_year_from": self._seerr_year_from,
            "seerr_year_to": self._seerr_year_to
        })

    def delete_history(self, doubanid: str, apikey: str):
        """
        删除同步历史记录
        """
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        # 历史记录
        historys = self.get_data('history')
        if not historys:
            return schemas.Response(success=False, message="未找到历史记录")
        # 删除指定记录
        historys = [h for h in historys if h.get("doubanid") != doubanid]
        self.save_data('history', historys)
        return schemas.Response(success=True, message="删除成功")

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))

    @staticmethod
    def __get_username_by_douban(user_id: str) -> Optional[str]:
        """
        根据豆瓣ID获取用户名
        """
        try:
            return UserOper().get_name(douban_userid=user_id)
        except Exception as err:
            logger.warn(f'{err}, 需要 MoviePilot v2.2.6+ 版本')
        return None

    def should_route_to_seerr(self, mediainfo) -> bool:
        """判断媒体是否匹配 Seerr 路由过滤器。"""
        if not getattr(mediainfo, "tmdb_id", None):
            return False
        if mediainfo.type == MediaType.MOVIE:
            if not self._seerr_movie:
                return False
        elif mediainfo.type == MediaType.TV:
            if not self._seerr_tv:
                return False
        else:
            return False

        if self._seerr_genre_ids:
            genre_ids = getattr(mediainfo, "genre_ids", None)
            if not genre_ids:
                return False
            if set(map(int, genre_ids)).intersection(self._seerr_genre_ids):
                return False

        if self._seerr_languages:
            language = getattr(mediainfo, "original_language", None)
            if not language or str(language).lower() not in self._seerr_languages:
                return False

        if self._seerr_year_from is not None or self._seerr_year_to is not None:
            try:
                year = int(str(getattr(mediainfo, "year", None)))
            except (TypeError, ValueError):
                return False
            if self._seerr_year_from is not None and year < self._seerr_year_from:
                return False
            if self._seerr_year_to is not None and year > self._seerr_year_to:
                return False
        return True

    @staticmethod
    def _seerr_response_message(response) -> str:
        """从 Seerr HTTP 响应中提取可读消息。"""
        try:
            data = response.json()
        except Exception:
            return response.text or "未知错误"
        if isinstance(data, dict):
            return data.get("message") or data.get("error") or response.text or "未知错误"
        return response.text or "未知错误"

    def _latest_seerr_season(self, request, tmdb_id: int) -> Optional[int]:
        """读取 Seerr 剧集详情并返回最高的非零季号。"""
        response = request.get_res(f"{self._seerr_host}/api/v1/tv/{tmdb_id}")
        if response is None or response.status_code != 200:
            return None
        try:
            seasons = response.json().get("seasons") or []
            return max(int(item.get("seasonNumber")) for item in seasons if int(item.get("seasonNumber") or 0) > 0)
        except (AttributeError, TypeError, ValueError):
            return None

    def request_seerr(self, mediainfo, meta) -> Tuple[bool, str]:
        """在 Seerr 创建请求，已存在的请求也视为已处理。"""
        if not self._seerr_host or not self._seerr_api_key:
            return False, "Seerr 地址或 API Key 未配置"

        request = RequestUtils(headers={
            "X-Api-Key": self._seerr_api_key,
            "Content-Type": "application/json"
        }, timeout=20)
        media_type = "movie" if mediainfo.type == MediaType.MOVIE else "tv"
        payload = {"mediaType": media_type, "mediaId": int(mediainfo.tmdb_id)}
        try:
            if mediainfo.type == MediaType.TV:
                season = meta.begin_season or self._latest_seerr_season(request, mediainfo.tmdb_id)
                if not season:
                    return False, "无法确定 Seerr 电视剧季号"
                payload["seasons"] = [int(season)]

            response = request.post_res(f"{self._seerr_host}/api/v1/request", json=payload)
            if response is None:
                return False, "Seerr 无响应"
            message = self._seerr_response_message(response)
            if response.status_code == 201:
                return True, "请求已创建"
            if media_type == "movie" and response.status_code == 409 and "already exists" in message.lower():
                return True, "请求已存在"
            if media_type == "tv" and response.status_code == 202 and "no seasons available" in message.lower():
                return True, "请求季已存在或可用"
            return False, f"Seerr 返回 HTTP {response.status_code}: {message}"
        except Exception as err:
            return False, str(err)

    def sync(self):
        """
        通过用户RSS同步豆瓣想看数据
        """
        if not self._users:
            return
        # 版本
        if hasattr(settings, 'VERSION_FLAG'):
            version = settings.VERSION_FLAG  # V2
        else:
            version = "v1"
        # 读取历史记录
        if self._clearflag:
            history = []
        else:
            history: List[dict] = self.get_data('history') or []
        for user_id in self._users.split(","):
            # 同步每个用户的豆瓣数据
            if not user_id:
                continue
            logger.info(f"开始同步用户 {user_id} 的豆瓣想看数据 ...")
            url = self._interests_url % user_id
            if version == "v2":
                results = RssHelper().parse(url, headers={
                    "User-Agent": settings.USER_AGENT
                })
            else:
                results = RssHelper().parse(url)
            if not results:
                logger.warn(f"未获取到用户 {user_id} 豆瓣RSS数据：{url}")
                continue
            else:
                logger.info(f"获取到用户 {user_id} 豆瓣RSS数据：{len(results)}")
            # 解析数据
            mediachain = MediaChain()
            downloadchain = DownloadChain()
            subscribechain = SubscribeChain()
            searchchain = SearchChain()
            subscribeoper = SubscribeOper()
            for result in results:
                try:
                    dtype = result.get("title", "")[:2]
                    title = result.get("title", "")[2:]
                    # 增加豆瓣昵称，数据来源自app.helper.rss.py
                    nickname = result.get("nickname", "")
                    if nickname:
                        nickname = f"[{nickname}]"
                    if dtype not in ["想看"]:
                        logger.info(f'标题：{title}，非想看数据，跳过')
                        continue
                    if not result.get("link"):
                        logger.warn(f'标题：{title}，未获取到链接，跳过')
                        continue
                    # 判断是否在天数范围
                    pubdate: Optional[datetime.datetime] = result.get("pubdate")
                    if pubdate:
                        if (datetime.datetime.now(datetime.timezone.utc) - pubdate).days > float(self._days):
                            logger.info(f'已超过同步天数，标题：{title}，发布时间：{pubdate}')
                            continue
                    douban_id = result.get("link", "").split("/")[-2]
                    # 检查是否处理过
                    if not douban_id or douban_id in [h.get("doubanid") for h in history]:
                        logger.info(f'标题：{title}，豆瓣ID：{douban_id} 已处理过')
                        continue
                    # 识别媒体信息
                    meta = MetaInfo(title=title)
                    douban_info = self.chain.douban_info(doubanid=douban_id)
                    meta.type = MediaType.MOVIE if douban_info.get("type") == "movie" else MediaType.TV
                    if settings.RECOGNIZE_SOURCE == "themoviedb":
                        tmdbinfo = mediachain.get_tmdbinfo_by_doubanid(doubanid=douban_id, mtype=meta.type)
                        if not tmdbinfo:
                            logger.warn(f'未能通过豆瓣ID {douban_id} 获取到TMDB信息，标题：{title}，尝试使用豆瓣数据识别')
                            mediainfo = self.chain.recognize_media(meta=meta, doubanid=douban_id)
                            if not mediainfo:
                                logger.warn(f'豆瓣ID {douban_id} 未识别到媒体信息')
                                continue
                        else:
                            mediainfo = self.chain.recognize_media(meta=meta, tmdbid=tmdbinfo.get("id"))
                            if not mediainfo:
                                logger.warn(f'TMDBID {tmdbinfo.get("id")} 未识别到媒体信息')
                                continue
                    else:
                        mediainfo = self.chain.recognize_media(meta=meta, doubanid=douban_id)
                        if not mediainfo:
                            logger.warn(f'豆瓣ID {douban_id} 未识别到媒体信息')
                            continue
                    # 查询缺失的媒体信息
                    exist_flag, no_exists = downloadchain.get_no_exists_info(meta=meta, mediainfo=mediainfo)
                    if exist_flag:
                        logger.info(f'{mediainfo.title_year} 媒体库中已存在')
                        action = "exist"
                    else:
                        # 用户转换
                        real_name = self.__get_username_by_douban(user_id)
                        if subscribechain.exists(mediainfo=mediainfo, meta=meta):
                            logger.info(f'{mediainfo.title_year} 正在 MoviePilot 订阅中')
                            action = "subscribe"
                        elif self.should_route_to_seerr(mediainfo):
                            handled, message = self.request_seerr(mediainfo, meta)
                            if not handled:
                                logger.error(f'Seerr 请求失败 {mediainfo.title_year}: {message}')
                                continue
                            logger.info(f'Seerr 已处理 {mediainfo.title_year}: {message}')
                            action = "seerr"
                        elif self._search_download:
                            # 先搜索资源
                            logger.info(
                                f'媒体库中不存在或不完整，开启搜索下载，开始搜索 {mediainfo.title_year} 的资源...')
                            # 按订阅优先级规则组搜索过滤，站点为设置的订阅站点
                            filter_results = searchchain.process(
                                mediainfo=mediainfo,
                                no_exists=no_exists,
                                sites=self.systemconfig.get(SystemConfigKey.RssSites),
                                rule_groups=self.systemconfig.get(SystemConfigKey.SubscribeFilterRuleGroups)
                            )
                            if filter_results:
                                logger.info(f'找到符合条件的资源，开始下载 {mediainfo.title_year} ...')
                                action = "download"
                                if mediainfo.type == MediaType.MOVIE:
                                    # 电影类型调用单次下载
                                    download_id = downloadchain.download_single(
                                        context=filter_results[0],
                                        username=real_name or f"豆瓣{nickname}想看"
                                    )
                                    if not download_id:
                                        logger.info(f'下载失败，添加订阅 {mediainfo.title_year} ...')
                                        self.add_subscribe(mediainfo, meta, nickname, real_name)
                                        action = "subscribe"
                                else:
                                    # 电视剧类型调用批量下载
                                    downloaded_list, no_exists = downloadchain.batch_download(
                                        contexts=filter_results,
                                        no_exists=no_exists,
                                        username=real_name or f"豆瓣{nickname}想看"
                                    )
                                    if no_exists:
                                        logger.info(f'下载失败或未下载完所有剧集，添加订阅 {mediainfo.title_year} ...')
                                        sub_id, message = self.add_subscribe(mediainfo, meta, nickname, real_name)
                                        action = "subscribe"

                                        # 更新订阅信息
                                        logger.info(f'根据缺失剧集更新订阅信息 {mediainfo.title_year} ...')
                                        subscribe = subscribeoper.get(sub_id)
                                        if subscribe:
                                            subscribechain.finish_subscribe_or_not(subscribe=subscribe,
                                                                                   meta=meta,
                                                                                   mediainfo=mediainfo,
                                                                                   downloads=downloaded_list,
                                                                                   lefts=no_exists)

                            else:
                                logger.info(f'未找到符合条件资源，添加订阅 {mediainfo.title_year} ...')
                                self.add_subscribe(mediainfo, meta, nickname, real_name)
                                action = "subscribe"
                        else:
                            logger.info(f'媒体库中不存在或不完整，未开启搜索下载，添加订阅 {mediainfo.title_year} ...')
                            self.add_subscribe(mediainfo, meta, nickname, real_name)
                            action = "subscribe"
                    # 存储历史记录
                    history.append({
                        "action": action,
                        "title": title,
                        "type": mediainfo.type.value,
                        "year": mediainfo.year,
                        "poster": mediainfo.get_poster_image(),
                        "overview": mediainfo.overview,
                        "tmdbid": mediainfo.tmdb_id,
                        "doubanid": douban_id,
                        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    })
                except Exception as err:
                    logger.error(f'同步用户 {user_id} 豆瓣想看数据出错：{str(err)}')
            logger.info(f"用户 {user_id} 豆瓣想看同步完成")
        # 保存历史记录
        self.save_data('history', history)
        # 缓存只清理一次
        self._clearflag = False

    @staticmethod
    def add_subscribe(mediainfo, meta, nickname, real_name):
        return SubscribeChain().add(
            title=mediainfo.title,
            year=mediainfo.year,
            mtype=mediainfo.type,
            tmdbid=mediainfo.tmdb_id,
            doubanid=mediainfo.douban_id,
            season=meta.begin_season,
            exist_ok=True,
            username=real_name or f"豆瓣{nickname}想看"
        )

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event):
        """
        豆瓣想看同步
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "douban_sync":
                return

            logger.info("收到命令，开始执行豆瓣想看同步 ...")
            self.post_message(channel=event.event_data.get("channel"),
                              title="开始同步豆瓣想看 ...",
                              userid=event.event_data.get("user"))
        self.sync()

        if event:
            self.post_message(channel=event.event_data.get("channel"),
                              title="同步豆瓣想看数据完成！", userid=event.event_data.get("user"))

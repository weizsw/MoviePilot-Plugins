from types import SimpleNamespace

import app.plugins.doubansyncmod as doubansyncmod
from app.plugins.doubansyncmod import DoubanSyncMod
from app.schemas.types import MediaType


def test_seerr_routing_filter_matches_only_eligible_media():
    """仅完整匹配过滤条件且具备 TMDB ID 的媒体应转交 Seerr。"""
    plugin = object.__new__(DoubanSyncMod)
    plugin._seerr_movie = True
    plugin._seerr_tv = True
    plugin._seerr_genre_ids = [16]
    plugin._seerr_languages = ["en", "ja"]
    plugin._seerr_year_from = 2020
    plugin._seerr_year_to = 2025

    def media(**overrides):
        """构造最小媒体信息并允许覆盖单个字段。"""
        values = {
            "type": MediaType.MOVIE,
            "tmdb_id": 123,
            "genre_ids": [18],
            "original_language": "en",
            "year": "2024",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    assert plugin.should_route_to_seerr(media()) is True
    assert plugin.should_route_to_seerr(media(type=MediaType.TV)) is True
    assert plugin.should_route_to_seerr(media(genre_ids=[18, 16])) is False
    assert plugin.should_route_to_seerr(media(original_language="ko")) is False
    assert plugin.should_route_to_seerr(media(year="2019")) is False
    assert plugin.should_route_to_seerr(media(original_language=None)) is False
    assert plugin.should_route_to_seerr(media(tmdb_id=None)) is False

    plugin._seerr_genre_ids = []
    plugin._seerr_languages = []
    plugin._seerr_year_from = None
    plugin._seerr_year_to = None
    assert plugin.should_route_to_seerr(media(genre_ids=[], original_language=None, year=None)) is True


def test_existing_seerr_request_is_handled(monkeypatch):
    """HTTP 409 的既有电影请求应被视为已处理。"""
    class Response:
        """模拟 requests 对 409 响应求布尔值为假的行为。"""
        status_code = 409
        text = '{"message":"Request for this media already exists."}'

        def __bool__(self):
            """保持与 requests.Response 的状态布尔语义一致。"""
            return False

        @staticmethod
        def json():
            """返回 Seerr 的重复请求消息。"""
            return {"message": "Request for this media already exists."}

    class Request:
        """捕获发送给 Seerr 的电影请求。"""
        def __init__(self, **_kwargs):
            """接受生产代码传入的请求配置。"""
            pass

        @staticmethod
        def post_res(_url, json):
            """校验请求体并返回重复响应。"""
            assert json == {"mediaType": "movie", "mediaId": 123}
            return Response()

    monkeypatch.setattr(doubansyncmod, "RequestUtils", Request)
    plugin = object.__new__(DoubanSyncMod)
    plugin._seerr_host = "http://seerr:5055"
    plugin._seerr_api_key = "secret"

    handled, message = plugin.request_seerr(
        SimpleNamespace(type=MediaType.MOVIE, tmdb_id=123),
        SimpleNamespace(begin_season=None),
    )

    assert (handled, message) == (True, "请求已存在")

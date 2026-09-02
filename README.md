# 豆瓣想看魔改版

MoviePilot V2 插件。它同步豆瓣“想看”RSS，并可按媒体类型、类型、原始语言和上映年份，将匹配项目转交给 Seerr；其他项目继续使用 MoviePilot 的搜索、下载和订阅流程。

插件 ID 是 `DoubanSyncMod`，可与官方 `DoubanSync` 同时安装。

## 安装

在 MoviePilot 的插件市场设置中添加仓库：

```text
https://github.com/weizsw/MoviePilot-Plugins
```

然后安装“豆瓣想看魔改版”。

## Seerr 路由

1. 填写 Seerr 地址和 API Key。
2. 选择要转交的电影和/或电视剧。
3. 按需配置屏蔽类型、允许的原始语言和上映年份范围。

所有已配置的条件必须同时匹配。媒体命中任一屏蔽类型时不会转交。缺少已配置条件所需的元数据时，项目继续走 MoviePilot 流程。

电视剧优先请求豆瓣标题中识别出的季；未识别到季时，请求 Seerr 返回的最高非特别篇季号。

## 测试

准备一个较新的 MoviePilot 源码目录后运行：

```bash
MOVIEPILOT_BACKEND_PATH=../MoviePilot pytest tests/v2/doubansyncmod
```

## 来源与许可

基于 MoviePilot 官方 `DoubanSync` V2 插件修改。项目按 [GNU GPL v3](LICENSE) 发布。

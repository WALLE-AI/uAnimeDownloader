# Anime Downloader Backend

这个后端为前端仪表盘提供动漫资源抓取数据。

## 启动步骤

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

成功后可以访问:
- 健康检查: http://127.0.0.1:8000/api/health
- 数据接口: http://127.0.0.1:8000/api/scrape

## 返回数据契约

### 成功 (HTTP 200)
接口 `/api/scrape` 返回 JSON 数组:
```json
[
  {
    "title": "【10月新番】葬送的芙莉莲 - 第07集 简体内嵌",
    "url": "magnet:?xt=urn:btih:fakehash1111",
    "size": "1.23 GB",
    "quality": "1080p WEBRip",
    "date": "2025-10-26T19:31:22.123456+00:00",
    "source": "某字幕组 · Nyaa"
  }
]
```

### 失败 / 无内容 (HTTP 200)
```json
{ "error": "No new anime releases today." }
```

> 注意：前端逻辑是：
> - 如果返回对象里有 `error` 字段，就当成失败。
> - 否则必须是数组。

## 字段说明

- `title`: 资源标题
- `url`: 磁链 / 下载链接
- `size`: 文件大小字符串
- `quality`: 清晰度或版本描述
- `date`: ISO8601 时间字符串 (JS 可以 `new Date(...)`)
- `source`: 来源站/字幕组

前端会：
1. 用 `date` 生成年月归档，比如 `📂 2025年10月`
2. 用整条数据模拟“下载队列”展示进度
3. 在表格里渲染资源清单
4. 写入系统日志

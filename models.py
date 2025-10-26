from pydantic import BaseModel, Field


class AnimeInfo(BaseModel):
    """
    单条动漫资源的结构（必须和前端保持一致）
    """
    title: str = Field(..., description="资源标题，比如『葬送的芙莉莲 第07集』")
    url: str = Field(..., description="下载地址/磁力链接/跳转链接")
    size: str = Field(..., description="文件大小，比如 '1.2 GB'")
    quality: str = Field(..., description="清晰度/版本，比如 '1080p WEB-DL'")
    date: str = Field(..., description="发布时间，ISO8601 格式字符串，前端会用 new Date() 解析")
    source: str = Field(..., description="来源站/字幕组信息，比如 '某字幕组 · Nyaa'")

class ErrorResponse(BaseModel):
    """
    错误响应结构。当前端收到 {error: "..."} 时，会认为 scrape 失败。
    """
    error: str

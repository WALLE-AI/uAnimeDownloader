from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Union

import uvicorn
from models import AnimeInfo, ErrorResponse
from crawler import mock_scrape_latest, scrape_comicat_today
load_dotenv()

app = FastAPI(
    title="Anime Downloader Backend",
    description="提供动漫资源抓取给前端仪表盘使用",
    version="0.1.0",
)

# -- CORS 设置 --
# 允许本地开发时的前端（Vite dev server / React dev server）来访问这个 API
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],   # e.g. GET, POST
    allow_headers=["*"],   # e.g. Authorization, Content-Type
)


@app.get("/api/health")
def health_check():
    """
    健康检查接口，前端或你自己可以用这个来判断后端是否跑起来了
    """
    return {"status": "ok"}


@app.get("/api/scrape")
def scrape_latest():
    """
    抓取动漫资源列表，让前端展示在表格里、归档、下载模拟等。

    前端期望的两种返回格式：
    1) 成功:
       [
         {
            "title": "...",
            "url": "...",
            "size": "...",
            "quality": "...",
            "date": "2025-10-26T19:31:22.123456+00:00",
            "source": "..."
         },
         ...
       ]

    2) 失败/无数据:
       {
         "error": "No new anime releases today."
       }
    """

    try:
        results = scrape_comicat_today()

        # 没抓到内容时，返回 {error: "..."}
        if not results:
            return {"error": "No new anime releases today."}

        # FastAPI 会自动把 List[AnimeInfo] 序列化成 JSON 数组
        return results[0]

    except Exception as e:
        # 出异常时也用 {error: "..."} 而不是抛500
        # 因为你的前端逻辑在等 'error' 字段
        return {"error": f"Scrape failed: {e}"}
    
    
if __name__ == "__main__":
    uvicorn.run(
        "main:app",           # ASGI app 路径
        host="127.0.0.1",     # 监听地址
        port=8001,            # 端口改成你要的 8001
        reload=True           # 热重载
    )

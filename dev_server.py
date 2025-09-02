#!/usr/bin/env python3
"""
开发服务器启动脚本 - 支持热重载
使用方法: python dev_server.py
"""

import uvicorn
import os
from pathlib import Path

if __name__ == "__main__":
    # 项目根目录 - 脚本在项目根目录，所以直接使用当前目录
    project_root = Path.cwd()
    
    # 配置 uvicorn 热重载
    uvicorn.run(
        "src.parlant.bin.server:start_parlant_app",  # 应用入口点
        host="0.0.0.0",
        port=8000,
        reload=True,  # 启用热重载
        reload_dirs=[
            str(project_root / "src"),  # 监听 src 目录
            str(project_root / "examples"),  # 监听 examples 目录
        ],
        reload_excludes=[
            "*.pyc",
            "*.pyo", 
            "*.pyd",
            "__pycache__",
            "*.log",
            ".git",
            ".env",
        ],
        log_level="info",
        access_log=True,
    )

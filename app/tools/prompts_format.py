
# 处理 action 中的 markdown 链接，提取并 decode 圆括号内容
import re
import urllib.parse

def decode_markdown_links(text, logger):
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    
    def replace_link(match):
        link_url = match.group(2)   # 圆括号内容
        decoded_url = urllib.parse.unquote(link_url)
        # suffix a space
        return f"{decoded_url} "
    
    try:
        # 匹配 [text](url) 格式，提取圆括号内容并 decode
        pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        return re.sub(pattern, replace_link, text)
    except Exception as e:
        # 如果正则匹配失败，返回原始文本
        logger.error(f"decode markdown links failed: {text}, error: {e}")
        return text
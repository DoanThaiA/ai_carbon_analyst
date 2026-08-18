import os
import re

replacements = [
    (r"crawl_services\.config", "core.config"),
    (r"crawl_services\.models", "schemas.crawl_models"),
    (r"crawl_services\.storage", "services.storage"),
    (r"crawl_services\.chunking", "services.chunking"),
    (r"crawl_services\.embedding", "services.embedding"),
    (r"crawl_services\.classification", "crawl_services.classification"),
    (r"crawl_services\.crawler", "crawl_services.crawler"),
    (r"crawl_services\.date_filter", "crawl_services.date_filter"),
    (r"crawl_services\.dedupe", "crawl_services.dedupe"),
    (r"crawl_services\.extraction", "crawl_services.extraction"),
    (r"crawl_services\.fetcher", "crawl_services.fetcher"),
    (r"crawl_services\.market_data", "crawl_services.market_data"),
    (r"crawl_services\.pipeline", "pipeline.crawl_pipeline"),
    (r"crawl_services", "crawl_services"), # fallback for anything else
]

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for pattern, replacement in replacements:
        new_content = re.sub(pattern, replacement, new_content)
    
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

def main():
    for root, dirs, files in os.walk("."):
        if ".git" in root or ".venv" in root or "__pycache__" in root:
            continue
        for file in files:
            if file.endswith(".py") or file.endswith(".md") or file.endswith(".yaml"):
                process_file(os.path.join(root, file))

if __name__ == "__main__":
    main()

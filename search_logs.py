keywords = ["delete", "remove", "clear", "clean", "删除", "清除", "移除", "accounts", "巡检"]

with open("logs/app.log", "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        # Check if line contains any keyword
        if any(kw in line.lower() for kw in keywords):
            print(line.strip())

"""ビルドスクリプト: deploy.zipを作成"""
import os
import shutil
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.join(BASE_DIR, "package")
OUTPUT_ZIP = os.path.join(BASE_DIR, "deploy.zip")


def build():
    # 1. appフォルダをpackage/にコピー
    app_dest = os.path.join(PACKAGE_DIR, "app")
    if os.path.exists(app_dest):
        shutil.rmtree(app_dest)

    # handlers/とservices/をコピー
    os.makedirs(os.path.join(app_dest, "handlers"), exist_ok=True)
    os.makedirs(os.path.join(app_dest, "services", "data"), exist_ok=True)

    # __init__.py
    shutil.copy(os.path.join(BASE_DIR, "__init__.py"), app_dest)
    shutil.copy(os.path.join(BASE_DIR, "handlers", "__init__.py"),
                os.path.join(app_dest, "handlers"))
    shutil.copy(os.path.join(BASE_DIR, "services", "__init__.py"),
                os.path.join(app_dest, "services"))

    # handlers
    shutil.copy(os.path.join(BASE_DIR, "handlers", "polling_handler.py"),
                os.path.join(app_dest, "handlers"))

    # services
    for f in ["violation_detector.py", "notion_client.py", "slack_notifier.py"]:
        shutil.copy(os.path.join(BASE_DIR, "services", f),
                    os.path.join(app_dest, "services"))

    # data
    for f in ["articles.json", "ng_patterns.json"]:
        shutil.copy(os.path.join(BASE_DIR, "services", "data", f),
                    os.path.join(app_dest, "services", "data"))

    # 2. deploy.zip作成
    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(PACKAGE_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, PACKAGE_DIR)
                z.write(file_path, arcname)

    size_mb = os.path.getsize(OUTPUT_ZIP) / 1024 / 1024
    print(f"Created deploy.zip: {size_mb:.1f} MB")
    print(f"Lambda handler: app.handlers.polling_handler.lambda_handler")


if __name__ == "__main__":
    build()

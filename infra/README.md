# Infra（AWS CDK / Python）

このディレクトリは、AWS CDK（Python）でインフラを定義します。  
チーム内で CDK CLI のバージョン差異によるトラブルを避けるため、コマンドは **`npx aws-cdk@2`** に統一します。

---

## 前提（共通）

- **Python**（推奨: 3.11 以上）
- **Node.js**（`npx` を使うため）
<!--
- **AWS 認証情報**（`deploy` や `diff` をする人のみ）
  - 例：AWS SSO / IAMユーザーのアクセスキー / Assume Role など
-->

---

## セットアップ（共通の流れ）

1. 仮想環境 `.venv` を作る
2. `.venv` を有効化（activate）
3. 依存関係をインストール
4. `synth`（テンプレ生成）で動作確認

---

## セットアップ（Windows / PowerShell）

```powershell
cd infra

python -m venv .venv
.venv\Scripts\activate

python -m pip install -U pip
python -m pip install -r requirements.txt

# Synth（テンプレ生成）
npx aws-cdk@2 synth
```


## セットアップ（macOS / Linux）

```bash
cd infra

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
python -m pip install -r requirements.txt

# Synth（テンプレ生成）
npx aws-cdk@2 synth
```
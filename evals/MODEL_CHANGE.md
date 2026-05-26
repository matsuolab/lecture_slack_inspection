# モデル変更 (Deprecation) 対応手順

目的: モデルの廃止（deprecation）に伴い、`evals` の評価を実行し、GitHub Variables にモデル名を設定する手順をまとめます。

**1) ローカルで評価を実行する**

1. README のセットアップ手順に従って、セットアップする

2. `promptfooconfig.yml` の `providers[0].id` を新しいモデル名に書き換える

3. promptfoo を使って評価を実行する

    ```bash
    cd evals/promptfoo
    npx promptfoo eval
    ```

**2) GitHub Variables にモデル名を設定する**
実際の環境にデプロイするために変更を行う

- リポジトリの `Settings > Security > Secrets and variables > Actions` を開く
- `Variables`のタブで`New repository variable` をクリック
- `Name` に `OPENAI_MODEL`、`Value` に例: `gpt-4o-mini` を入力して保存

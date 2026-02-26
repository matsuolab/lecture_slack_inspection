# Notion Database Schema: Violation Logs

Slack監視Botが違反投稿を記録・管理するためのNotionデータベース定義です。

## 概要

- **Database Name**: `Community Violation Logs` (任意)
- **Description**: ガイドライン違反検知ログおよび対応ステータスの管理
- **System**:
  - **Write**: Lambda A (app_inspect) - 新規作成
  - **Update**: Lambda B (app_alert) - ステータス更新

## プロパティ定義

| プロパティ名         | タイプ       | 必須 | 説明                                                                                 | 選択肢 / 設定                                                                                     |
| :------------------- | :----------- | :--- | :----------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------ |
| **投稿内容**         | Title        | YES  | 投稿内容の抜粋（検索・見出し用）。<br>※文字数制限あり（100文字推奨）                 | -                                                                                                 |
| **対応ステータス**   | Select       | YES  | 対応状況。<br>Lambda Bによりボタン操作で更新される。                                 | **Options**:<br>- `Unprocessed` (初期値)<br>- `Approved` (警告送信済)<br>- `Dismissed` (対応不要) |
| **判定結果**         | Select       | YES  | 違反判定の結果カテゴリ。                                                             | **Options**:<br>- `Violation`<br>- `Safe`<br>etc.                                                 |
| **検出方法**         | Select       | YES  | 検知に使用した手法。                                                                 | **Options**:<br>- `OpenAI`<br>- `NGWord`                                                          |
| **信頼度**           | Number       | NO   | AI判定の信頼度（0.0 - 1.0）。                                                        | 表示形式: `Percent`                                                                               |
| **該当条文**         | RichText     | NO   | 違反と判定された根拠となる条文ID。<br>Feat版機能の復活項目。                         | -                                                                                                 |
| **投稿者**           | RichText     | YES  | 投稿者のSlack User ID（または表示名）。                                              | -                                                                                                 |
| **チャンネル**       | RichText     | YES  | 投稿されたチャンネル名。                                                             | -                                                                                                 |
| **ワークスペース**   | RichText     | NO   | 投稿されたワークスペース名。                                                         | -                                                                                                 |
| **投稿リンク**       | URL          | YES  | Slackの元投稿へのPermlink。                                                          | -                                                                                                 |
| **検出日時**         | Date         | YES  | 検知日時（ISO 8601）。                                                               | -                                                                                                 |
| **対応者**           | RichText     | NO   | ボタンを押した管理者のSlack User ID。<br>Lambda Bにより記録される。                  | -                                                                                                 |
| **警告送信日時**     | Date         | NO   | 警告送信が実行された日時（ISO 8601）。<br>Lambda Bにより `Approved` 時に記録される。 | -                                                                                                 |
| **リマインド送信済** | Checkbox     | NO   | 削除依頼リマインド送信後にtrueにする。<br>Lambda C（将来実装）用。                   | -                                                                                                 |
| **違反理由**         | RichText     | NO   | 違反と判定された理由の詳細（AIの出力）。                                             | -                                                                                                 |
| **違反カテゴリ**     | Multi-select | NO   | 該当する違反のカテゴリ。                                                             | -                                                                                                 |
| **重大度**           | Select       | NO   | 違反の重大度。                                                                       | **Options**:<br>- `low`<br>- `medium`<br>- `high`                                                 |

---

## 運用上の注意（Developers Note）

### ステータス値の統一

プロパティ名（キー）は日本語ですが、**対応ステータスの選択肢（値）は英語**（`Unprocessed`, `Approved`, `Dismissed`）で統一して運用します。
これに伴い、Lambda側のコード（`common/notion_client.py`, `app_alert/handler.py`）内のステータス文字列定数を修正する必要があります。

### オプショナル項目

`信頼度` および `該当条文` は、Lambda A (app_inspect) の判定ロジック修正により値が供給されることを前提としています。

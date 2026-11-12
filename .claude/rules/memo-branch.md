# 作業記録はメモ用のブランチ `claude/memo` に置く

Claude が次のセッション（次の Claude）のために残す**作業記録**は、成果物の作業ブランチではなく、メモ用のブランチ `claude/memo` に置く。
2026-11-12 にユーザーが決めた。

## 形

- `claude/memo` は、コードと履歴を共有しない独立したブランチ（orphan）1 本を使い続ける
- worktree として `/home/hiroto/DX_PJT/lecture_slack_inspection_memo` に出しておく。作業ブランチを切り替えずに読み書き・commit できる
  - worktree が無いとき: `git -C /home/hiroto/DX_PJT/lecture_slack_inspection worktree add /home/hiroto/DX_PJT/lecture_slack_inspection_memo claude/memo`
- **push しない（ローカルだけ）**。commit はユーザーに頼まれなくてよい
- 一覧は memo の `README.md`。セッションの始めに引継ぎを探すときは、まずここを見る

## 置くもの・置かないもの

| 置く（`claude/memo`） | 置かない（作業ブランチに置く／どこにも置かない） |
|---|---|
| 引継ぎ書（`handover/YYYYMMDD_<テーマ>.md`） | 成果物: `docs/` の文書（要件・仕様・調査の記録）、テスト、道具、コード → 作業ブランチ |
| Gemini への指示書の写し、learn-from-impl の判断の記録（`pending/YYYYMMDD/`） | 根拠（再現テスト・照合の道具・集計のスクリプト）→ 作業ブランチ（`.claude/rules/evidence.md`） |
| | 受講生のデータ、秘密情報、本番の ID、Gemini のログの全文 → どこにも置かない |

迷ったら「運営・CI・他の開発者が使うか」で分ける。使うなら成果物（作業ブランチ）、次の Claude しか読まないなら作業記録（`claude/memo`）。

## 作業ブランチの commit

- main 以外の作業ブランチなら、Claude は区切りごとに commit してよい（2026-11-12 にユーザーが許可）。main では commit しない
- commit の前に `git branch --show-current` で main でないことを確かめる。以前からある無関係の変更（ステージ済みのものを含む）は、パスを指定して外す
- push・PR のマージ・デプロイはしない（`.claude/rules/gemini-delegation.md`）

import re
from typing import Optional, Tuple

# モジュール内で使用する定数（外部から直接参照しない場合は _ 付きでもOK）
_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
    "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16,
}

def parse_article_id(article_id: str) -> Tuple[Optional[int], Optional[int]]:
    """
    規約IDをパースして (条数, 項数) のタプルを返す共通関数。
    ※ 実際の実装は、現在ご自身のコードに書かれているものをそのまま移植してください。
    """
    if not article_id:
        return None, None
    
    match = re.match(r"第(\d+)条(?:-([a-z]+))?", article_id)
    if not match:
        return None, None

    article_num = int(match.group(1))
    clause_roman = match.group(2)
    
    clause_num = None
    if clause_roman and clause_roman in _ROMAN:
        clause_num = _ROMAN[clause_roman]
        
    return article_num, clause_num
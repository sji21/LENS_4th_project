"""보유한 주택임대차 대법원 판례 26건을 공통 SQLite 형식으로 적재한다.

법령은 이 스크립트에서 변경하지 않는다. 판례 청크만 추출하므로 이후 단계에서
법령 청크와 합친 뒤 KURE-v1 Chroma 컬렉션을 한 번에 색인해야 한다.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database.relational import connect_database, initialize_relational_database
from src.ingestion.load_cases import CaseRecord, export_case_chunks, load_case_records


DEFAULT_DATABASE = ROOT / "data" / "database" / "knowledge.sqlite3"
DEFAULT_EXPORT = ROOT / "data" / "chunks" / "cases.jsonl"


@dataclass(frozen=True)
class Source:
    source_id: str
    source_type: str
    title: str
    content: str
    question_ids: tuple[int, ...]
    court_name: str = ""
    decision_date: str = ""
    case_number: str = ""


# 모두 국가법령정보센터에서 확인한 주택임대차 관련 대법원 판례다. content는
# 원문 전체가 아닌 Retriever 평가용 쟁점 요약이며, 서비스에서는 원문 청크로 교체한다.
SOURCES = (
    Source("CASE-2015DA254507", "case", "배당이의", "외국인등록과 체류지 변경신고 또는 국내거소신고와 거소이전신고는 주택임대차보호법상 주민등록과 같은 대항요건 효과를 가질 수 있다. 전입신고·주민등록·대항력 취득 쟁점의 주택임대차 판례다.", (1, 2), "대법원", "2019-04-11", "2015다254507"),
    Source("CASE-2024DA326398", "case", "임대차보증금반환", "주택 임차인이 인도와 주민등록으로 대항력을 취득한 뒤 점유를 상실하면 대항력도 소멸한다. 이사 전에 임차권등기가 마쳐지지 않으면 이전 대항력이 소급 회복되지 않는다. 보증금 미반환·임차권등기명령·경매 쟁점이다.", (2, 5, 6, 9), "대법원", "2025-04-15", "2024다326398"),
    Source("CASE-2011DA49523", "case", "추심금", "대항력 있는 주택임대차에서 임차주택이 양도되면 특별한 사정이 없는 한 양수인이 임대인의 지위를 승계한다. 집주인이 바뀐 경우 보증금반환채무의 귀속을 다룬 전원합의체 판결이다.", (3,), "대법원", "2013-01-17", "2011다49523"),
    Source("CASE-2021DA238650", "case", "구상금등청구의소", "공동임차인 중 1명이라도 주택의 인도와 주민등록으로 대항력을 갖추면 그 대항력은 임대차 전체에 미친다. 주택 양도 시 공동임차인 전부에 대한 보증금반환채무는 양수인에게 이전된다.", (3,), "대법원", "2021-10-28", "2021다238650"),
    Source("CASE-2025DA210305", "case", "배당이의", "전세임대주택에서 법인 임차인이 입주자의 인도와 주민등록으로 대항력·우선변제권을 취득한 뒤 입주자가 주택 소유자가 되면 대항력이 소멸한다. 주민등록의 공시 기능과 대항력 존속을 다룬 판례다.", (2, 4), "대법원", "2026-02-26", "2025다210305"),
    Source("CASE-2025DA213466", "case", "보증금", "주택 임차인이 임차주택을 매수해 소유자가 되면 임차인 주민등록은 더 이상 임차권을 공시하지 못해 대항력·우선변제권이 소유권 취득 시 소멸한다. 대항요건의 계속 존속을 확인한 판례다.", (2, 4), "대법원", "2026-01-08", "2025다213466"),
    Source("CASE-2009DA101275", "case", "배당이의", "대지와 신축건물이 함께 경매된 경우 확정일자부 임차인과 소액임차인의 우선변제 범위, 소액보증금 범위의 기준시점을 판단했다. 임대인 동의를 얻은 임차권 양수인·전차인의 우선변제권 행사도 다뤘다.", (1, 4, 22, 24), "대법원", "2010-06-10", "2009다101275"),
    Source("CASE-94DA37646", "case", "배당이의", "임대차 기간 중 임차주택이 경매되면 대항력 있는 임차인이 임대차를 해지하고 우선변제를 청구할 수 있는지, 배당요구의 의미가 무엇인지 판단한 주택임대차 판례다.", (4, 5, 9), "대법원", "1996-07-12", "94다37646"),
    Source("CASE-98CHU40", "case", "마산시주택임대차계약증서확정일자부여업무조례무효확인", "주택임대차계약증서 확정일자 부여 업무의 법적 성격과 지방자치단체 조례의 적법성을 다뤘다. 확정일자 제도와 우선변제권의 연결을 이해하기 위한 판례다.", (1, 18), "대법원", "1999-04-13", "98추40"),
    Source("CASE-2021DA266631", "case", "건물인도", "임차인이 계약갱신을 요구했더라도 임대인 또는 임대인 지위를 승계한 주택 양수인이 법정 기간 안에 실제 거주하려는 사유를 들어 갱신을 거절할 수 있다고 봤다.", (12, 13), "대법원", "2022-12-01", "2021다266631"),
    Source("CASE-2022DA279795", "case", "건물인도", "계약갱신요구권은 임차인의 주거안정과 임대인의 재산권 사이의 조화를 위한 제도다. 임대인이 실제 거주를 이유로 갱신을 거절하려면 실제 거주 의사에 관한 증명책임을 부담한다.", (12, 13), "대법원", "2023-12-07", "2022다279795"),
    Source("CASE-2020DA202371", "case", "건물명도(인도)", "아무 통지 없이 계약이 자동 연장되는 묵시적 갱신 상황에서, 민간임대주택의 임대인은 법령·표준임대차계약에서 정한 갱신거절 사유가 없으면 임차인이 갱신을 원하는 때 갱신을 거절할 수 없다고 판단했다. 갱신된 임대차기간, 묵시적 갱신과 갱신거절을 다룬 판례다.", (10, 12), "대법원", "2020-05-28", "2020다202371"),
    Source("CASE-2007DA17475", "case", "배당이의", "주택임대차보호법상 우선변제를 받기 위한 주택 인도와 주민등록은 취득 때뿐 아니라 민사집행법상 배당요구 종기까지 계속 존속해야 한다고 판단했다.", (4, 22, 24), "대법원", "2007-06-14", "2007다17475"),
    Source("CASE-2004DA69741", "case", "건물명도", "주택 임차인이 전세권설정등기를 추가로 했더라도 주택임대차보호법상 인도·주민등록 대항요건을 상실하면 대항력과 우선변제권을 상실한다. 전세권과 주택임대차 우선변제권은 별개라는 취지다.", (2, 4, 22), "대법원", "2007-06-28", "2004다69741"),
    Source("CASE-2009DA40790", "case", "손해배상(기)", "주택임차인이 임차인 지위와 전세권자 지위를 함께 가진 경우 임차인으로서 한 배당요구가 전세권자로서의 배당요구까지 뜻하는 것은 아니라고 판단했다. 임차권 우선변제와 전세권 우선변제의 구별 쟁점이다.", (4, 22), "대법원", "2010-06-24", "2009다40790"),
    Source("CASE-2000DA61466", "case", "배당이의", "재경매에서는 대항력·우선변제권의 인도와 주민등록 요건이 배당금의 기초가 되는 최종 경락기일까지 유지되어야 한다고 판단했다.", (4, 22, 24), "대법원", "2002-08-13", "2000다61466"),
    Source("CASE-97DA43468", "case", "배당이의", "주택임대차 대항력은 인도와 주민등록을 계속 유지해야 하며, 임차인이 다른 곳으로 전출했다가 재전입하면 기존 대항력은 소멸하고 소급 회복되지 않는다고 판단했다.", (2, 5, 6), "대법원", "1998-01-23", "97다43468"),
    Source("CASE-95DA30338", "case", "배당이의", "주택임대차 대항요건의 주민등록에는 임차인 본인뿐 아니라 배우자·자녀 등 가족의 주민등록도 포함된다. 가족이 점유와 주민등록을 유지한 채 임차인만 일시 전출한 경우의 대항력도 다뤘다.", (2,), "대법원", "1996-01-26", "95다30338"),
    Source("CASE-2000DA44799", "case", "배당이의", "주택임대차 대항요건인 주민등록은 제3자가 임차권 존재를 인식할 수 있는 공시방법이어야 한다. 임차주택이 아닌 인접 토지 지번으로 주민등록을 한 경우 유효한 공시방법이 아니라고 판단했다.", (2,), "대법원", "2001-04-24", "2000다44799"),
    Source("CASE-2000DA24184", "case", "임대차보증금·건물명도", "실제 주거 사용·수익 목적 없이 기존 채권을 우선변제 받으려고 외관만 만든 가장 주택임대차에는 주택임대차보호법상 대항력을 인정할 수 없다고 판단했다.", (2, 20), "대법원", "2002-03-12", "2000다24184"),
    Source("CASE-96DA17653", "case", "건물명도", "임대인이 소유권을 취득했다가 계약해제로 소유권을 잃더라도 그 전에 주택 인도와 주민등록으로 대항요건을 갖춘 임차인은 새로운 소유자에게 임차권을 대항할 수 있다고 판단했다.", (2, 3), "대법원", "1996-08-20", "96다17653"),
    Source("CASE-99DA59306", "case", "전부금", "경매로 소멸하는 선순위 저당권보다 후순위인 임차권은 경락인에게 주장할 수 없다. 주민등록이 임대차를 공시하는 유효한 방법이 되기 위한 기준과 전 소유자가 임차인이 된 경우 대항력 취득 시점도 판단했다.", (2, 3, 4), "대법원", "2000-02-11", "99다59306"),
    Source("CASE-98DA32939", "case", "건물명도", "경매로 소멸하는 선순위 저당권보다 뒤에 대항력을 갖춘 주택임차권은 함께 소멸해 경락인에게 주장할 수 없다고 판단했다. 주민등록 공시와 소유자에서 임차인으로 전환된 경우도 다뤘다.", (2, 3, 4), "대법원", "1999-04-23", "98다32939"),
    Source("CASE-97DA22393", "case", "배당이의", "인도와 주민등록을 마친 날 또는 그 전에 확정일자를 받았더라도 우선변제권은 대항력과 마찬가지로 인도와 주민등록을 마친 다음 날을 기준으로 발생한다고 판단했다.", (1, 4, 18, 22), "대법원", "1997-12-12", "97다22393"),
    Source("CASE-95DA44597", "case", "배당이의", "소액임차인의 최우선변제권을 위한 인도와 주민등록은 우선변제권 취득 때에만 갖추면 되는 것이 아니라 배당요구 종기인 경락기일까지 계속 존속해야 한다고 판단했다.", (4, 22, 24), "대법원", "1997-10-10", "95다44597"),
    Source("CASE-93DA39676", "case", "건물명도", "대항요건을 갖춘 주택임차인은 임차주택 양수인에게 보증금 반환 때까지 임대차 존속을 주장하는 권리와 주택 가액에서 우선변제를 받을 권리를 함께 가지며, 둘 중 하나를 선택해 행사할 수 있다고 판단했다.", (3, 4, 5, 22), "대법원", "1993-12-24", "93다39676"),
)

def case_url(case_number: str) -> str:
    from urllib.parse import quote

    return "https://www.law.go.kr/LSW/precInfoP.do?evtNo=" + quote(case_number)


def records_from_sources() -> list[CaseRecord]:
    """기존 26건 판례 시드를 공통 판례 적재 형식으로 바꾼다."""

    return [
        CaseRecord(
            case_id=source.source_id,
            case_number=source.case_number,
            court_name=source.court_name,
            decision_date=source.decision_date,
            case_type="민사",
            case_name=source.title,
            holding=source.content,
            summary=source.content,
            full_text=source.content,
            source_url=case_url(source.case_number),
            collected_at="2026-08-28",
            file_path=f"cases/{source.source_id}.md",
        )
        for source in SOURCES
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="주택임대차 대법원 판례를 공통 SQLite에 적재합니다.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--export", type=Path, default=DEFAULT_EXPORT)
    args = parser.parse_args()

    initialize_relational_database(args.database)
    with connect_database(args.database) as connection:
        summary = load_case_records(records_from_sources(), connection)
        exported = export_case_chunks(connection, args.export)
        citations = connection.execute("SELECT COUNT(*) FROM case_law_citations").fetchone()[0]
    print(f"SQLite: {args.database}")
    print(f"Supreme Court housing cases: {summary.cases}")
    print(f"Case chunks: {exported}")
    print(f"case_law_citations: {citations} (MVP에서는 적재하지 않음)")


if __name__ == "__main__":
    main()

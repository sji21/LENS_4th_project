# 등기사항증명서 주의 신호 점검

## 현재 구현 범위

현재 초기 기능은 사용자가 올린 PDF에서 계약 전 확인이 필요한 권리 문구를 찾아 Streamlit 화면에 표시한다.

```text
PDF 업로드
  -> 파일 형식·크기·페이지 검증
  -> PDF 내장 텍스트 추출
  -> 텍스트가 부족한 페이지만 Tesseract OCR
  -> 주민등록번호·전화번호·계좌번호 마스킹
  -> 결정론적 위험 신호 규칙
  -> 발견 근거·추가 확인사항·공식 참고자료 표시
  -> 후속 RAG 검색 질의 생성
```

현재 기능은 LLM이나 외부 OCR API를 사용하지 않는다. 업로드 원본과 추출문을 서버 파일로 저장하지 않고 Streamlit 세션 메모리에서만 처리한다.

## 탐지하는 주요 신호

- 갑구: 경매개시결정, 압류·가압류, 가처분, 신탁, 가등기
- 을구: 근저당권·저당권, 전세권, 임차권등기, 근질권, 공동담보
- 문서 상태: 확인된 발급일이 30일을 초과한 경우 최신 문서 재확인 안내

탐지 결과는 `우선 확인`, `주의`, `주요 키워드 미탐지`, `판독 보류`로 표현한다. “안전한 집”, “계약 가능” 같은 결론은 출력하지 않는다.

## Tesseract 설치

### macOS

```bash
brew install tesseract tesseract-lang
```

설치 확인:

```bash
tesseract --list-langs
```

출력에 `kor`와 `eng`가 있어야 한다.

### Windows

1. Windows용 Tesseract를 설치한다.
2. 설치 시 Korean 언어 데이터를 선택한다.
3. `tesseract.exe` 폴더를 PATH에 추가한다.
4. PATH를 사용할 수 없다면 `.env`의 `TESSERACT_CMD`에 실행 파일의 전체 경로를 지정한다.

```dotenv
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

## 실행

```bash
python -m venv .venv
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

## 테스트

```bash
pytest -q
```

로컬 비공개 샘플이 `test/data/부동산등기부등본.pdf`에 있거나 `REGISTRY_SAMPLE_PDF` 환경 변수로 지정되면 OCR 통합 테스트도 실행한다. 원본 등기 PDF는 Git에 커밋하지 않는다.

Windows 팀원 검증 명령과 결과 기록표는 `docs/windows-verification.md`를 사용한다.

## 후속 LangChain·LangGraph 연결

`DocumentAnalysis.rag_queries`에는 발견된 권리별 공식 근거 검색 질의가 들어간다. 후속 개발은 다음 경계를 유지한다.

1. `src/document_check/`: OCR·구조화·규칙 기반 위험 신호
2. LangChain Retriever: 법령·정부 가이드 검색
3. LangGraph: `ANSWER`, `ABSTAIN`, `REFUSE` 상태 라우팅과 필요 시 1회 재검색
4. LLM: 검색 컨텍스트 안에서만 설명 생성
5. 출처 조합: LLM이 아니라 코드가 검색 메타데이터로 생성

## 알려진 한계

- OCR은 흐린 스캔, 도장, 세로쓰기, 복잡한 표에서 문구를 누락할 수 있다.
- 키워드 존재 여부만으로 권리의 효력·순위·말소 여부를 완전히 판단할 수 없다.
- 등기 이후 변경, 미납세금, 선순위 임차보증금 등은 업로드 문서만으로 확인할 수 없다.
- 실제 계약 전에는 최신 등기를 다시 발급하고 공인중개사·HUG·법률 전문가에게 확인해야 한다.

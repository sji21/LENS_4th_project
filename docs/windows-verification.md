# Windows 재현 검증

등기 PDF 업로드 기능은 macOS와 Windows에서 같은 Python 코드로 동작한다. Tesseract 실행 파일 설치 방식만 운영체제별로 다르다.

## 준비

PowerShell에서 다음 명령을 실행한다.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows용 Tesseract와 Korean 언어 데이터를 설치하고 확인한다.

```powershell
tesseract --version
tesseract --list-langs
```

출력에 `kor`와 `eng`가 있어야 한다. PATH를 사용할 수 없다면 `.env` 또는 현재 PowerShell 세션에 경로를 지정한다.

```powershell
$env:TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## 테스트

개인정보가 제거되었거나 팀 내부 검증이 허용된 등기 PDF의 경로를 지정한다. 원본 PDF는 Git에 커밋하지 않는다.

```powershell
$env:REGISTRY_SAMPLE_PDF="C:\private-data\registry-sample.pdf"
pytest -q
```

확인 기준:

- 전체 단위 테스트 통과
- `test_registry_pdf_end_to_end` 통과 또는 허용된 샘플이 없을 때만 skip
- OCR 페이지가 `unreadable`이 아님
- 화면과 JSON에 주민등록번호·전화번호·계좌번호 원문이 없음
- 발견 신호에 페이지·근거 문구·추가 확인사항이 표시됨

## 앱 실행

```powershell
streamlit run app/streamlit_app.py
```

브라우저에서 PDF 업로드, 동의 체크, 점검 실행, JSON 다운로드를 확인한다.

## 검증 기록

| 환경 | Python | Tesseract | 단위 테스트 | 통합 PDF | Streamlit | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| macOS arm64 | 3.13.13 | 로컬 설치 | 통과 | 6페이지 통과 | AppTest·서버 통과 | 완료 |
| Windows | 팀원 기록 필요 | 팀원 기록 필요 | 팀원 기록 필요 | 팀원 기록 필요 | 팀원 기록 필요 | 검증 대기 |

Windows 행이 실제 결과로 갱신되기 전까지 `PATCH-005`는 완료 처리하지 않는다.

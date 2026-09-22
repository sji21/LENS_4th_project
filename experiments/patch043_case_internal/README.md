# PATCH-043 내부 판례 검색 실험 보관본

이 디렉터리는 제품 채택 대상이 아닌 PATCH-043 판례 검색 실험의 코드와 평가 도구를 보관한다.
LENS의 Django 서비스와 `src/retrieval/` 제품 경로는 이 패키지를 import하거나 실행하지 않는다.

실험의 별도 8,395건 후보 데이터, 모델, 독립 평가 자료는 저장소에 포함되어 있지 않다. 따라서 이
디렉터리의 존재는 해당 후보의 운영 성능, 배포 가능성 또는 독립 평가 완료를 의미하지 않는다.

재현 연구가 필요한 경우에만 명시적으로 실행한다.

```powershell
python -m pytest experiments/patch043_case_internal/tests -q
python -m experiments.patch043_case_internal.scripts.case_internal_query --help
```

제품 검색에는 검증된 MySQL portable release 또는 `lens-case-retrieval-v1` 판례 release만 사용한다.

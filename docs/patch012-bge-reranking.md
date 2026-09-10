# BGE 전용 재정렬 모델 실험 — 미채택

## 판단

**BGE-reranker-v2-m3의 이번 설정은 채택하지 않는다.** GPU에서 실행 가능했고 후보 20개 정렬은 중앙값 0.402초였지만, 필수 법령 보유 30문항의 문맥 포함 TOP5가 기존 저장 결과 26개에서 21개로 낮아졌다. 부정문 TOP3의 지정 금지 조문 반환은 없었으나, 양성 대조 문항의 필수 근거 반환도 낮았다. 부정문 문제 해결이나 운영 개선으로 보고하지 않는다.

## 무엇을 바꿔 시험했는가

기존 KURE·Chroma·BM25로 수집한 후보를 고정하고 **질문과 조문 본문을 함께 입력하는 전용 모델**로 점수만 매겼다. 새로운 임베딩 생성, DB 재구축, 법령 추가 적재, 답변 생성은 하지 않았다. 모델이 출력한 점수는 관련성 정렬용이며 법률적 정답 확률이 아니다.

- 모델: `BAAI/bge-reranker-v2-m3`, revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.
- 공식 모델은 다국어 관련성 점수화용이며 Apache 2.0 라이선스다. [공식 모델 카드](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- 새 GPU 환경은 `tmp/venv-bge`, 모델 파일은 `tmp/bge-reranker-v2-m3`. 기존 `.venv`와 운영 requirements는 변경하지 않았다.
- PyTorch `2.7.1+cu128`, Transformers `4.51.3`, RTX 3060 Laptop 6GB, FP16, 배치 2.
- 최대 길이 2048. 길이 초과 시 중단하며 질문·조문을 조용히 자르지 않는다.
- 후보: 일반10+민법5, 일반13+민법7. 20개 후보를 한 번 점수화하고 15개 구성은 해당 후보만 남겨 정렬한다. 따라서 15개 후보만 따로 실행한 지연은 측정하지 않았다.
- 점수 임계값이나 자동 보류 기능 없이 TOP3/5를 비교했다. 민법 강제 우선·특정 조 번호 지정은 없다.
- 입력 질문·후보 캡처·법령 140개 본문 해시를 검증했다. 입력·가중치·실행기 해시는 결과 JSON에 기록했다. 질문과 법령 본문은 외부 API로 전송하지 않았다.

## 결과

200입력 전체를 실행했으며 아래 공식 비교 분모는 **고정 필수 법령을 모두 보유하고 과거 판본 별도 확인 대상이 아닌 30문항**이다. 나머지 70문항을 성공으로 처리하지 않는다. 여러 필수 조문이 있으면 모두 들어가야 성공이다. TOP5는 TOP3를 포함한다.

| 방식 | 질문 단독 TOP3 | 질문 단독 TOP5 | 문맥 포함 TOP3 | 문맥 포함 TOP5 |
|---|---:|---:|---:|---:|
| 기존 PATCH-012 저장 결과 | 21 | 22 | 24 | 26 |
| 일반10+민법5 → BGE | 16 | 20 | 17 | 21 |
| 일반13+민법7 → BGE | 15 | 21 | 16 | 21 |

기존은 동일 입력을 사용한 저장 결과이며 이번에 운영 검색 전체를 재실행한 값은 아니다. 재정렬에서 무엇이 달라지는지 살피는 개발 실험이며 독립 평가, 판례·안내 평가, LLM 답변 품질 평가는 아니다.

문맥 포함 20개 후보에서 DEV-057의 필수 민법 제626조는 4위, DEV-058의 제627조는 15위, DEV-059의 제626조는 19위였다. 필요한 조문이 후보에 있어도 충분히 올리지 못했다. DEV-089의 후보 단계 누락도 그대로 남았다.

### 대조 입력 24개

9개는 지정 금지 조문 반환을, 나머지 15개는 지정 필수 근거의 전부 반환을 점검한다. 두 분모를 합쳐 하나의 정확도로 만들지 않는다. 기존 공개 개발 문항에서 파생된 입력을 포함하므로 독립 평가가 아니다.

| 후보 구성 | 금지 조문 TOP3 반환 / 9 | 금지 조문 TOP5 반환 / 9 | 양성 필수 근거 TOP3 / 15 | 양성 필수 근거 TOP5 / 15 |
|---|---:|---:|---:|---:|
| 일반10+민법5 | 0 | 3 | 3 | 6 |
| 일반13+민법7 | 0 | 2 | 3 | 6 |

TOP3 금지 조문 반환이 0이어도 올바른 관련성 판정이 완성됐다는 뜻은 아니다. 필수 근거를 함께 밀어낸 결과일 수 있으므로 양성 성능을 같이 봐야 한다. 두 구성 모두 “비용을 돌려달라는 뜻은 아니에요/아닙니다”의 제626조가 TOP5에 남았고, 작은 구성에서는 “아닌데요”도 남았다.

## 시간과 자원

- DEV 200입력, 후보20 재정렬 중앙값 **0.402초**, p95 **0.478초**, 최대 **0.879초**.
- 모델 적재 약 **5.02초**, 별도 예열 약 **0.67초**. 위 입력당 시간에서 제외했다.
- PyTorch 최대 할당 **1,202,184,704 bytes**, 최대 예약 **1,256,194,048 bytes**. 전체 GPU 점유량이나 KURE·답변 LLM과 동시 실행할 때의 메모리를 뜻하지 않는다.
- 저장 후보를 사용했으므로 검색·답변 생성 시간은 포함하지 않는다. 모델을 계속 유지하는 운영 형태나 동시 사용자 부하를 검증하지 않았다.

## 검증과 재현

반례 24개와 DEV 200입력이 모두 오류 없이 완료됐다. 원본 청크 파일 전후 해시가 일치하며 DB 연결 자체를 하지 않았다. 관련 실험 계약 테스트 **28 passed**. 제품 코드·기존 `.venv`·운영 DB·질문·정답을 변경하지 않았다. 제품 전체 테스트는 이번에 재실행하지 않았다.

실험 환경은 기존 Python에서 새로 만든다. GPU 패키지는 [PyTorch 공식 배포 경로](https://pytorch.org/get-started/locally/)를 사용했다.

```powershell
.venv/Scripts/python -m venv tmp/venv-bge
tmp/venv-bge/Scripts/python -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu128
tmp/venv-bge/Scripts/python -m pip install transformers==4.51.3 sentencepiece safetensors --index-url https://pypi.org/simple
```

모델은 위 revision에서 `config.json`, `tokenizer*`, `special_tokens_map.json`, `sentencepiece.bpe.model`, `model.safetensors`만 다운로드한다. 원격 사용자 코드는 실행하지 않는다. [직전 후보 캡처](patch012-candidate-sweep.md)를 준비한 후 새 출력 폴더로 실행한다.

```powershell
tmp/venv-bge/Scripts/python scripts/retrieval_experiments/bge_rerank.py --source tmp/patch012-candidate-sweep --model tmp/bge-reranker-v2-m3 --out tmp/bge-probes-new --stage probes
tmp/venv-bge/Scripts/python scripts/retrieval_experiments/bge_rerank.py --source tmp/patch012-candidate-sweep --model tmp/bge-reranker-v2-m3 --out tmp/bge-dev-new --stage dev
.venv/Scripts/python scripts/retrieval_experiments/report_bge.py --dev tmp/bge-dev-new --probes tmp/bge-probes-new --out tmp/bge-summary-new.json
.venv/Scripts/python -m pytest tests/test_bge_rerank.py tests/test_candidate_pipeline.py tests/test_candidate_sweep.py tests/test_rank_candidates.py -q
```

전체 패키지 버전은 [환경 기록](patch012-bge-environment.txt), 수치·프로토콜·200입력 순위·반례·해시는 [실험 결과](patch012-bge-reranking.json)에 보존했다. 원시 후보 점수는 `tmp/patch012-bge-dev/`, `tmp/patch012-bge-probes/`에 있으며 새 클론에는 포함되지 않는다. 다운로드 모델·가상환경은 Git 제외 대상이다.

현재 결과로는 새 모델을 연결할 근거가 부족하다. 기존 검색을 유지하고, 후보 누락 개선과 문맥·관련성 판정 방식을 별도로 검토한다. 추가 학습이나 모델 교체의 효과도 이번 결과로 단정하지 않는다. PATCH-012는 초안·병합 보류다.

# DeepSeek 전환 및 EDA 검토 성능 — 2026-10-18

`ppa-agent`의 실제 사용 모델은 **`deepseek/deepseek-v4.1-flash-1017`**, provider는 OpenRouter, reasoning effort는 medium이다. 작업 시작 시 프로필 파일은 이미 이 모델로 설정되어 있었다. 실제 대시보드 요청과 저장된 세션의 모델 식별자까지 확인했다.

**이번 소규모 평가에서 GLM 대비 일관된 성능 개선은 확인하지 못했다.** DeepSeek은 기본 증거 검토와 단일 도구 호출 선택을 처리했지만, 계산 문제에서는 출력 예산과 도구 사용 지시를 주의해서 검토할 필요가 있다. 요청한 모델은 그대로 적용해 두었다.

## 동일 조건 API 비교

같은 저장소 AGENTS.md와 같은 문제 6개를 각 모델에 두 번씩 제공했다. 총 24회이며 temperature=0, reasoning=medium, max_tokens=2048, 동시 요청 2개, 자동 재시도 없음이다. 순서는 seed 20261018로 섞었다. 서로 다른 tokenizer와 자동 선택된 provider, 캐시 상태는 실제 서비스 조건의 일부이며 동일하게 고정하지 않았다.

| 항목 | 기존 GLM 5.3 Flash | DeepSeek V4.1 Flash 1017 |
| --- | ---: | ---: |
| HTTP 성공 | 12/12 | 12/12 |
| 검토 문제 전체 통과 | 12/12 | 11/12 |
| 전체 응답 시간 중앙값 | 1.906초 | 1.756초 |
| 전체 응답 시간 평균 | 1.957초 | 3.413초 |
| 최장 전체 응답 시간 | 3.603초 | 12.223초 |
| 입력 토큰 | 28,688 | 30,028 |
| 출력 토큰(추론 포함) | 2,578 | 6,520 |
| 그중 추론 토큰 | 431 | 3,871 |
| API가 반환한 사용 비용 합계 | $0.003992 | $0.004615 |

비용은 카탈로그 최저 단가로 추정한 값이 아니라 각 응답의 `usage.cost` 합계다. 별도 청구서 대조는 하지 않았다. 입력 캐시 토큰은 GLM 10,240, DeepSeek 18,816이다. 따라서 이 값에서 모델 고유의 가격 차이나 통계적으로 안정적인 지연 차이를 분리할 수 없다. 위 시간은 요청부터 전체 응답 수신까지이며 첫 토큰 지연이나 생성 속도가 아니다.

| 문제 | 증거 출처 | GLM | DeepSeek |
| --- | --- | ---: | ---: |
| 면적·전력·코어 면적 변화율 및 tradeoff | 실제 counter4 결과 | 2/2 | 1/2 |
| Magic DRC·timing corner·equivalence 누락 시 판정 | 명시적 가상 fixture | 2/2 | 2/2 |
| 첫 실패 단계 및 PDN-0185 식별 | 실제 실패 로그 | 2/2 | 2/2 |
| OpenLane 2 density 및 SCL 후보 계약 | 저장소 지침 | 2/2 | 2/2 |
| legacy provenance와 SRAM prepare-only 한계 | 명시적 가상 fixture | 2/2 | 2/2 |
| 보고서 조회 함수 및 인자 선택 | 실행하지 않는 단일 도구 fixture | 2/2 | 2/2 |

DeepSeek 계산 문제의 실패는 `finish_reason: length`, completion_tokens=2048, content=null이었다. 네 변화율 모두 올바르게 반환한 다른 반복에서는 1,879 출력 토큰과 12.223초를 사용했다. 두 계산 요청의 지연이 DeepSeek의 평균을 크게 높였다. 기본 증거 검토에서 빠른 응답이 관찰되어도 계산까지 항상 빠르다고 일반화할 수 없다.

초기 자동 채점은 `OpenROAD.GeneratePDN`과 `GeneratePDN`을 문자열로 구분하여 양쪽 모델의 올바른 답을 각각 두 번씩 오답 처리했다. 문제는 정규화된 식별자가 아닌 단계명을 요청했으므로, 양쪽에 동일하게 별칭을 인정한 점수를 위에 사용했다. 원래 응답·자동 점수는 변경하지 않았고 `audited-summary.json`에 보정 근거를 별도로 남겼다. 문제 전체 통과 수를 사용하며, 파싱 실패 때문에 분모가 달라지는 원시 세부 체크 비율은 모델 비교 점수로 사용하지 않는다.

## 출력 예산 추가 확인

최초 24회 결과를 보존한 채 계산 문제만 **max_tokens=4096으로 모델당 한 번씩**, 총 2회 추가 요청했다. 나머지 요청 조건은 같았다. 이 결과는 위 표에 합산하지 않았다.

* DeepSeek: 3.584초, 1,004 출력 토큰, 모든 계산 통과.
* GLM: 4.155초, 399 출력 토큰. 셀 면적과 전력의 곱 변화율을 +1.60%로 잘못 반환했다. 실제 값은 약 −3.2604%이다.

따라서 출력 한도 확대 시 DeepSeek이 완료할 수 있음을 한 번 확인했으나, 이것만으로 실패가 항상 해소되거나 GLM보다 정확하다고 판단할 수 없다. 샘플 간 변동이 크며 물리 수치 계산은 독립 계산으로 확인하는 편이 적절하다. 운영 프로필의 추론 강도나 출력 예산을 이 추가 확인 결과로 자동 변경하지 않았다.

## 대시보드 실제 경로 확인

최초 `POST http://127.0.0.1:8123/diagnose`는 공유 게이트웨이를 거쳐 401로 실패했다. 공유 게이트웨이의 `ppa-agent` upstream은 8655 포트를 가리켰지만, 그 포트의 실제 `/v1/models`는 `voc-agent`를 광고했고 ppa-agent 인증을 거부했다. 저장된 upstream key와 ppa-agent 프로필 key 자체는 같았다. 즉 최초 추정한 단순 키 불일치보다 정확한 원인은 잘못된 서비스 연결이다. ppa-agent launch 서비스 재시작만으로는 해결되지 않았다.

다른 프로젝트의 공유 게이트웨이 설정은 변경하지 않았다. 이 저장소의 `server/hermes-gateway.mjs`를 127.0.0.1:18700에서 시작하고, 무시되는 로컬 `.env`에 `HERMES_GATEWAY_PORT=18700`, `PPA_EDA_GATEWAY_BASE_URL=http://127.0.0.1:18700`를 저장했다. 실행 중인 EDA 작업이 없음을 확인한 뒤 대시보드 API를 재시작했다. 이후 시작할 때도 `node server/hermes-gateway.mjs`는 이 로컬 설정을 읽는다. API와 전용 게이트웨이 프로세스는 사용 가능하도록 실행 상태로 두었다. 새 로그인/재부팅 시 자동 시작 서비스 설치는 수행하지 않았다.

전용 게이트웨이 간단 확인은 7.853초에 성공했다. 이후 대시보드 `/diagnose` 요청은 **HTTP 200, 17.216초**에 완료했다. 해당 세션 DB에는 model=`deepseek/deepseek-v4.1-flash-1017`, billing_provider=`openrouter`, API 호출 2회, 입력 25,573 토큰, 출력 1,370 토큰이 기록되었다. 이 경로는 Hermes persona와 도구 문맥을 포함하므로 위 직접 API 비교보다 무겁다. 같은 경로로 GLM을 비교한 표본은 없으며, 경로 간 수치를 모델 간 속도 차이로 해석하지 않는다.

실제 진단은 cell-area×power **3.2604% 감소**, core area **35.94% 증가**를 정확히 설명했다. 다만 도구를 사용하지 말라는 요청에도 `execute_code` 호출을 한 번 시도했다. Hermes가 unattended session 정책으로 **차단해 코드 실행은 없었다**. 이 차단을 우회하거나 승인 설정을 완화하지 않았다. 수치 정확성과 도구 사용 지시 준수를 구분해서 평가해야 한다. 답변에 언급된 수율·물리 비용 등의 가능한 영향은 새로 검증한 물리 결과가 아니다.

## 해석과 재현 자료

이 평가는 주로 짧은 계약 준수 문제이며 저장소 규칙을 system prompt에 제공했다. 두 반복, 일부 가상 fixture, 단일 함수 선택만으로 장기 탐색·복잡한 SRAM 검토·여러 단계 도구 실행의 우열을 판단할 수 없다. 통계적 우월성 검정도 하지 않았다. **새 OpenLane 실험은 수행하지 않았으므로 모델 변경에 따른 실제 PPA 개선 효과는 미측정이다.** 기존 counter4 측정치를 얼마나 올바르게 해석하는지만 확인했다.

* `protocol.json`: 고정된 프롬프트, 문제, 정답 기준, 순서, 예산.
* `source.json`: 실제 case 파일 hash와 사용한 측정 발췌.
* `0-*.json`, `1-*.json`: 실제 요청·응답·provider·usage·원시 채점.
* `summary.json`, `audited-summary.json`: 원시 요약과 대칭적인 단계명 별칭 보정.
* `supplemental-protocol.json`, `supplemental-0.json`, `supplemental-1.json`: 4096 토큰 추가 확인.
* `gateway-check.json`, `gateway-response.txt`, `gateway-tool-audit.json`: 최종 실제 앱 경로, 선택 모델, 도구 시도와 차단.
* `gateway.log`, `gateway-retry.log`: 기존 연결 실패 기록. `gateway-final.log`: 수정 후 결과.
* `configuration-actions.json`: 비밀 값 없는 적용 내역. API key는 산출물에 저장하지 않았다.

모델 존재와 지원 파라미터는 당시 [OpenRouter 모델 페이지](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017)와 모델 목록 API로 확인했으며, 성능 결론은 위 로컬 요청 기록을 사용했다. `catalog.json`은 당시 모델 목록의 관련 두 항목만 보존한다.

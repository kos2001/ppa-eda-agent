# EDA 시각화 참고 도구에서 적용한 기능

2026-10-04에 공식 문서와 원본 저장소를 다시 확인하고, 현재 프로젝트의
저장된 측정값과 DEF 도형으로 구현할 수 있는 기능을 적용했다.

| 참고 자료 | 확인한 기능 | 현재 적용 |
| --- | --- | --- |
| [SiliconCompiler dashboard](https://docs.siliconcompiler.com/en/v0.38.3/user_guide/tutorials/dashboard_tutorial.html) | 실행·노드별 지표, 선택 노드 파일, 실행 간 그래프 | 후보 선택, 지표 비교 표, 면적·전력 산점도, 실행 단계 스냅샷, 선택 기록 JSON 내보내기 |
| [OpenROAD Web Viewer](https://openroad.readthedocs.io/en/latest/main/src/web/README.html) | 레이아웃 확대·이동, 표시 레이어, 경로 강조, 히트맵 | 기존 DEF 뷰어의 확대·이동, 레이어 선택, 실제 인스턴스·넷 검색과 강조, 셀 면적 분포 |
| [LanEx](https://github.com/AkshatIsWired/lanex) | 도구 출처로 연결되는 지표, 검증 근거, 미확인 상태 | 검사별 근거 매트릭스, 검사 클릭 → 원본 metrics 검색·줄 강조, 보고서와 resolved 설정 열기 |

원본 프로젝트 코드를 가져오거나 설치하지 않았으며 도구 체인을 변경하지
않았다. 현재 UI와 backend API를 확장했다. UI를 열거나 보고서를 확인하면
기존 파일을 읽으며 ASIC·SPICE flow를 실행하지 않는다.

## 사용 방법

1. http://127.0.0.1:5173 의 Overview에서 **Run workspace**를 연다.
2. 설계와 기록된 PDK·SCL·tool image를 고른다. 기본으로 최근 후보 4개가
   표시되며 **Choose candidates**에서 최대 6개를 선택할 수 있다.
3. 지표 표의 후보나 산점도를 누르면 상세 대상이 바뀐다. 면적·전력 쌍이
   없는 후보는 그래프에서 제외하고 표에서는 `—`로 남긴다.
4. 검증 매트릭스의 숫자를 누르면 원본 `final/metrics.json`에서 그 검사
   키가 강조된다. **Matching lines only**로 해당 줄만 볼 수 있다.
5. 흐름 스냅샷 노드를 선택해 해당 단계의 보고서·로그를 확인한다.
   **Run inputs**에는 실제 `resolved.json` 등이 있다.
6. **Interactive layout**에서 저장된 DEF 도형을 불러온다. 확대·축소,
   드래그 이동, 방향키 이동, 레이어 숨김, 셀·넷 검색과 강조를 사용할 수 있다.
   **Fit die**는 초기 화면으로 돌아간다.
7. **Open this case**는 선택한 과거 케이스를 정확히 연다. 최신 케이스로
   바꾸지 않는다. **Export selected JSON**은 현재 선택한 원시 관측값을 저장한다.

한국어·영어, light·dark theme, 390 px 모바일 화면을 지원한다. 기존 회로
예제 카드와 Layout Pipeline의 DEF 뷰어도 유지한다.

## 데이터 의미

비교 화면은 기록된 기술과 도구를 우선 분리한다. 같은 기술이어도 RTL,
실제 SDC, PDK·라이브러리 버전 등이 같다는 뜻은 아니다. 기록된 완전한
출처 키가 모두 같을 때만 호환으로 표시한다. 출처가 미확인 또는 다르면
원시 관측값으로 명시하며 개선률, 순위, 새로운 파레토 front를 계산하지 않는다.
이 화면의 관측값과 엄격한 평가 아카이브는 역할이 다르다.

전력 그래프는 기록된 vectorless 전력만 사용한다. 활동 기반 전력을 섞지
않는다. 진짜 측정값 0은 보존하고 누락값·비유한 값은 0으로 채우지 않는다.
확인되지 않은 setup slack을 WNS 0으로 대신하지 않는다.

검사 매트릭스는 실제 검사별 count를 쓴다. `0`은 기록된 위반 없음,
양수는 기록된 위반, `—`는 미확인이다. 예산 때문에 실행하지 못한 후보는
도구 실패가 아니다. 모델 자격이 열려 있으면 별도로 표시한다. 물리 count가
0이어도 SRAM 모델이나 기능 정확성을 확정하지 않는다.

실행 단계는 실제 디렉터리 번호 순서다. `state_out.json` 존재 여부는 저장된
상태 스냅샷 여부이며 검사 통과 판정이 아니다. 원본 파일은 현재 디스크에
남은 내용이며 수정 시각을 표시한다. 과거 케이스 파일의 측정값과 달라질 수
있으므로 불변 아카이브 사본이라고 해석하지 않는다.

셀 면적 분포는 DEF 인스턴스의 사각형을 16×16 격자에 정확히 잘라,
각 격자에서 `셀 면적 합 / 격자 면적`으로 계산한다. die 밖 면적은 제외한다.
겹친 셀은 합산되어 100%를 넘을 수 있다. 색은 100% 이상에서 포화되지만
원래 값은 도형 tooltip에 남긴다. 이 값은 라우팅 혼잡도, IR drop, DRC
또는 공정 수율의 측정값이 아니다.

현재 OpenROAD 문서의 WebSocket tile renderer나 타이밍 경로 보고서는
프로젝트의 pinned binary에 존재한다고 가정하지 않았다. 실제 STA 경로
좌표와 혼잡도·IR-drop 격자 데이터가 기록되어 있지 않아 그 시각화는
생성하지 않았다. 지금의 넷 강조는 실제 DEF 라우팅 도형을 표시한다.

## Backend 계약

`GET /reference-db/artifacts?file=<case.json>&tag=<candidate>`는 텍스트 산출물
목록과 단계 스냅샷을 반환한다. 이 목록의 `id`를 추가하면 내용을 읽는다.

허용 파일은 `metrics.json`, `resolved.json`, `state_in.json`, `state_out.json`,
`.log`, `.rpt`다. 기록된 실행 디렉터리는 workspace의 design/runs 또는
기존 `/private/tmp/ppa-…/…/runs/…`에 속해야 한다. 임의 host path나 링크로
다른 파일을 읽지 않는다. 목록은 2,000개, 내용은 첫 200 KB로 제한하며
잘림 여부를 명시한다. UI는 최대 1,500줄을 표시하고 반환된 내용에서 검색한다.
HTML을 실행하지 않고 텍스트로 표시한다.

원본 실행 디렉터리를 지웠으면 목록은 `run_available: false`다. 저장된
케이스 지표는 그대로 표시하고 원본 파일이 없다고 알린다. DEF 도형은
기존 candidate-detail API로 layout 탭을 열 때만 가져온다.

## 검증

전체 Python 테스트 1,183개 통과, 기존 환경 조건에 따른 4개 건너뜀.
TypeScript/Vite production build 통과. Lint 오류 0개, 기존 Fast Refresh
경고 7개 유지.

새 검증은 격자 분할 전후의 셀 면적 보존, die 밖 clipping, 미확인 검사와
출처 분리, 파일 경로 이동·symlink 차단, 큰 파일의 제한된 읽기를 확인한다.
실제 AES 원본 디렉터리에서 단계 88개·텍스트 산출물 551개와 final metrics를
확인했다. 단계 목록 88개에는 실행 단계 87개와 별도 final 산출물 노드가
포함된다. 이 개수는 해당 기록의 관측값이며 UI에 고정하지 않았다.

브라우저에서 검사 클릭 → 원본 줄 강조, JSON 저장, 레이어 숨김, 확대,
방향키 이동, 도형 검색·강조, 과거 케이스 이동, 원본 파일 부재 표시를
확인했다. 영어 desktop, 한국어 mobile, light theme에서 페이지 오류와
가로 넘침이 없었다. 진행 중인 SRAM characterization은 변경하지 않았다.

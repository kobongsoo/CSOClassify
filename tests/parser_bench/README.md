# 파서 비교 측정 스크립트 (순수 문서파서)

`report/파서비교-자체파서-vs-사이냅-202609.html` 을 만드는 스크립트다.
자체 파서(MpowerClassify-rs `--text-only`) 와 사이냅 문서필터(`snf_exe -U8`) 에
**같은 일(문서를 열어 글자를 뽑아 파일로 쓰기)만** 시켜 시간과 본문을 견준다.
분류·PII 검사는 양쪽 다 하지 않는다.

## 실행 순서

```bash
python bench_pure.py     # 표본 추출 → 두 파서 실행(각 3회, 최솟값) → raw_pure/pure.jsonl
python stats_pure.py     # 확장자별 집계 → raw_pure/pure_stats.json
python gen_report.py     # report/파서비교-자체파서-vs-사이냅-202609.html 생성
```

비교 기준(`norm`/`toks`)만 고쳤을 때는 다시 측정할 필요가 없다 —
두 파서의 본문이 `raw_pure/snftext` · `raw_pure/rstext` 에 남아 있다:

```bash
python recompute.py      # 저장된 본문으로 일치도만 다시 계산(시간은 그대로)
python stats_pure.py; python gen_report.py
```

## 경로

- 문서 원본: `D:\분류함`
- 사이냅: `resources/synap/windows/snf_exe.exe`
- 자체판: `Rust/dist-onedir/windows/MpowerClassify-rs.exe`

표본은 난수 씨앗이 고정돼 있어 다시 돌려도 같은 305건이 뽑힌다.

## 보고서의 '고치기 전' 수치

`gen_report.py` 의 `BEFORE` 는 PPTX 노트·XLSX 메모를 구현하기 **전** 파서로 뽑은
본문에 지금과 같은 잣대를 적용해 다시 계산한 값이다(측정 방식 변화가 섞이지 않게).
다시 확인하려면 해당 커밋 이전으로 되돌려 빌드한 exe 로 `bench_pure.py` 를 돌리면 된다.

## 1차 측정(분류 포함)

`v1_with_classification/` 참고 — 등급·PII 일치율까지 본 초기 비교다.

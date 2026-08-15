# -*- mode: python ; coding: utf-8 -*-
#------------------------------------------------------------------
# PyInstaller 스펙 (onefile, A안: 외부 모델 + 외부 사이냅) — 최소 단일 exe
#=> 모델도 사이냅 바이너리도 exe 에 넣지 않고 "코드 + onnxruntime" 만 단일 exe 로
#   묶는다. 이유:
#    - onefile 은 실행마다 내부를 임시(_MEI)폴더에 푸는데, 상주 데몬이 그 임시
#      폴더의 snf_exe.exe 를 참조하면 정리 레이스로 사라져 실패한다(실측 확인).
#      → 사이냅 바이너리를 exe 옆(안정 경로)에 두면 데몬이 안전하게 참조한다.
#    - 모델(대용량)도 exe 밖에 두어 매 실행 temp 해제 비용을 없앤다.
#   배포 구성: csoclassify.exe + synap/{snf_exe.exe, snf_win.dll} + e5-small-ko.tar.xz
#   런타임이 사이냅은 exe 옆에서, 모델은 tar.xz 를 최초 1회 캐시에 풀어 사용한다.
#   빌드:  pyinstaller build/csoclassify-onefile.spec
#------------------------------------------------------------------

import os
import sys

# ko-pii 사전(.txt.gz) 데이터만 훅으로 수집(collect_submodules 는 무거운 의존성을
# 끌어와 배제 — onedir 스펙 주석 참조). numpy 는 서브모듈까지 명시 수집해야 일부
# 환경(예: Linux numpy 2.x)에서 'numpy._core._exceptions' 누락을 막는다.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(os.getcwd()))
entry = os.path.join(ROOT, "src", "csoclassify_launcher.py")

# [규칙셋 외장화, 2026-08] 사이냅·모델뿐 아니라 C/S/O 규칙셋(cso_rules.yaml)도
# exe 에 내장하지 않는다. 런타임에 default_rules_path() 가 'exe 옆'(exe_dir)의 외장
# cso_rules.yaml 을 읽으므로, 재빌드 없이 규칙만 교체·배포할 수 있다(배포 시 exe 옆에 둔다).
datas = list(collect_data_files("ko_pii"))   # ko-pii 사전(.txt.gz 등)만 번들

# classify(분류) 의존성(yaml/ko-pii)과 서브모듈을 명시해 지연 import 누락을 막는다.
hiddenimports = [
    "onnxruntime", "tokenizers", "numpy",
    "yaml",
    "csoclassify.classify",
    "csoclassify.classify.rules",
    "csoclassify.classify.engine",
    "csoclassify.classify.context",
    "csoclassify.classify.fuse",
    # ko-pii 는 정적 import 추적으로 자동 포함(collect_submodules 불필요·유해).
]
# numpy 서브모듈 전수 수집(_core._exceptions 등 런타임 지연 import 누락 방지).
# 리눅스 한정: Windows 는 기본 훅으로 충분하고, 전수 수집은 exe 를 크게 만든다.
if os.name != "nt":
    hiddenimports += collect_submodules("numpy")

# 리눅스(특히 CentOS 7 등 구형 배포판) 전용: 시스템 libstdc++ 가 numpy 확장이 요구하는
# CXXABI_1.3.9+ 를 제공 못 해 ImportError 가 난다. conda 환경(빌드 파이썬)의 최신
# libstdc++/libgcc 를 _MEI 루트에 명시 번들해 시스템 구형 라이브러리 대신 쓰게 한다.
# (Windows 는 os.name=='nt' 라 건너뛰어 영향 없음)
binaries = []
if os.name != "nt":
    for _lib in ("libstdc++.so.6", "libgcc_s.so.1"):
        _p = os.path.join(sys.prefix, "lib", _lib)
        if os.path.exists(_p):
            binaries.append((_p, "."))

a = Analysis(
    [entry],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["torch", "transformers", "optimum", "sentence_transformers", "scipy", "matplotlib",
              "pandas", "pyarrow", "sklearn", "scikit-learn", "PIL", "Pillow", "datasets",
              "langchain", "langchain_text_splitters", "llama_index", "presidio_analyzer",
              "spacy", "openai", "hf_xet", "huggingface_hub",
              # 슬림화: 런타임에 안 쓰는 numpy 테스트/f2py, GUI(tkinter) 제외.
              "numpy.tests", "numpy._core.tests", "numpy.f2py", "numpy.distutils",
              "tkinter"],
)

pyz = PYZ(a.pure)

# 슬림화: 리눅스는 번들 .so 심볼을 strip 해 용량을 줄인다(strip 존재 확인됨).
# Windows 는 strip 도구가 없을 수 있어 끈다(os.name=='nt').
_strip = os.name != "nt"

# onefile: 바이너리·데이터를 EXE 안에 모두 포함(COLLECT 없음).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="csoclassify",
    console=True,
    strip=_strip,        # 리눅스: 심볼 제거로 감량
    upx=False,           # UPX 는 DLL 호환/백신 오탐 이슈로 기본 off
    onefile=True,
)

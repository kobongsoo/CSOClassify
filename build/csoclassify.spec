# -*- mode: python ; coding: utf-8 -*-
#------------------------------------------------------------------
# PyInstaller 스펙 (onedir, 외장 리소스) — 설계서 §10
#=> csoclassify.exe(+_internal/)를 만든다. 코드와 ko-pii 사전만 번들하고,
#   사이냅·임베딩 모델·규칙셋(cso_rules.yaml)은 exe 옆 '외장'으로 둔다(dist-pkg 스타일).
#   런타임에 resources.py 가 exe 옆(synap/<os>/, models/<name>/, cso_rules.yaml)에서 찾는다.
#   빌드:  pyinstaller build/csoclassify.spec  → dist/csoclassify/ (onedir, 빠른 시작)
#   ※ 리눅스 실행파일은 리눅스에서 빌드해야 한다(PyInstaller 크로스컴파일 불가).
#------------------------------------------------------------------

import os
import sys

# ko-pii(L1 PII 엔진) 사전(.txt.gz)만 훅으로 수집. collect_submodules(ko_pii) 는
# 무거운 비-검출 모듈까지 끌어와 배제하고, 정적 import 추적에 맡긴다.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(os.getcwd()))
entry = os.path.join(ROOT, "src", "csoclassify_launcher.py")

# [외장화] 사이냅·모델·규칙셋은 exe 에 번들하지 않는다(exe 옆 외장 배치). ko-pii 사전만 포함.
# 런타임 resources.py 가 exe 옆 synap/<os>/·models/<name>/·cso_rules.yaml 을 찾는다.
datas = list(collect_data_files("ko_pii"))

hiddenimports = [
    "onnxruntime", "tokenizers", "numpy", "yaml",
    "csoclassify.classify",
    "csoclassify.classify.rules",
    "csoclassify.classify.engine",
    "csoclassify.classify.context",
    "csoclassify.classify.fuse",
    # ko-pii 는 정적 import 추적으로 자동 포함(collect_submodules 불필요·유해).
]
# numpy 서브모듈 전수 수집(리눅스 한정): numpy._core._exceptions 등 지연 import 누락 방지.
# Windows 는 기본 훅으로 충분하고, 전수 수집은 용량만 키운다.
if os.name != "nt":
    hiddenimports += collect_submodules("numpy")

# 리눅스(CentOS 7 등 구형 배포판) 전용: 시스템 libstdc++ 가 numpy 확장이 요구하는
# CXXABI_1.3.9+ 를 제공 못 해 ImportError 가 난다. conda 환경(빌드 파이썬)의 최신
# libstdc++/libgcc 를 명시 번들해 시스템 구형 라이브러리 대신 쓰게 한다.(Windows 는 건너뜀)
binaries = []
if os.name != "nt":
    for _lib in ("libstdc++.so.6", "libgcc_s.so.1"):
        _p = os.path.join(sys.prefix, "lib", _lib)
        if os.path.exists(_p):
            binaries.append((_p, "."))

block_cipher = None

a = Analysis(
    [entry],
    pathex=[os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 런타임에 불필요한 대형 패키지는 제외해 용량을 줄인다.
    excludes=["torch", "transformers", "optimum", "sentence_transformers", "scipy", "matplotlib",
              "pandas", "pyarrow", "sklearn", "scikit-learn", "PIL", "Pillow", "datasets",
              "langchain", "langchain_text_splitters", "llama_index", "presidio_analyzer",
              "spacy", "openai", "hf_xet", "huggingface_hub",
              # 슬림화: 런타임에 안 쓰는 numpy 테스트/f2py, GUI(tkinter) 제외.
              "numpy.tests", "numpy._core.tests", "numpy.f2py", "numpy.distutils", "tkinter"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 슬림화: 리눅스는 번들 .so 심볼을 strip 해 용량을 줄인다. Windows 는 strip 도구가
# 없을 수 있어 끈다(os.name=='nt').
_strip = os.name != "nt"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir: 바이너리는 아래 COLLECT 로 모음
    name="csoclassify",
    console=True,            # CLI 도구이므로 콘솔 유지
    disable_windowed_traceback=False,
    strip=_strip,
    upx=False,               # UPX 는 DLL 호환성 확인 후 선택
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=_strip,
    upx=False,
    name="csoclassify",
)

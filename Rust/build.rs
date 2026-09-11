//! 빌드 스크립트 — pdfium 동적 라이브러리를 실행파일 옆으로 자동 복사한다.
//!
//! [왜 필요한가] `extract.rs::pdfium_lib_path()` 의 탐색 순서는
//! `CSO_PDFIUM` 환경변수 → **exe 옆** → 시스템이다. 개발 빌드(`target/debug`)에는
//! 이 라이브러리를 놓아 주는 사람이 없어서, 아무 설정 없이 실행하면 PDF 가 전부
//! 추출 실패로 떨어진다. 그런데 그 실패는 예외가 아니라 `extract_text` 가 `None` 을
//! 돌려주는 조용한 경로라, 결과만 보면 "이 PDF 들은 원래 안 읽히는 문서"처럼 보인다
//! — 원인이 드러나지 않는 종류의 고장이라 사람이 오래 헤맨다.
//!
//! 그래서 빌드할 때마다 배포본에 들어 있는 정본을 exe 옆으로 옮겨 둔다.
//! `cargo clean` 으로 target/ 이 통째로 지워져도 다음 빌드에서 저절로 복구된다.
//!
//! [실패해도 빌드를 깨지 않는다] 원본이 없는 환경(배포본을 안 받은 새 clone, CI)
//! 에서도 빌드 자체는 되어야 한다. 못 찾거나 못 복사하면 `cargo:warning` 만 남기고
//! 넘어간다 — 그때는 `CSO_PDFIUM` 으로 직접 지정하면 된다.

use std::path::{Path, PathBuf};

//------------------------------------------------------------------
// OUT_DIR → 실행파일이 놓이는 폴더
//=> cargo 는 빌드 산출 폴더를 build.rs 에 직접 알려 주지 않는다. 대신
//   OUT_DIR 이 `target/<프로파일>/build/<패키지>-<해시>/out` 형태라, 세 단계
//   올라가면 exe 가 놓이는 `target/<프로파일>` 이 된다.
//   (`--target <triple>` 을 준 경우에도 `target/<triple>/<프로파일>` 이 되어
//   같은 셈이 맞는다.)
//
// -in: 없음(환경변수 OUT_DIR 을 읽는다)
//
// -out: Option<PathBuf> = 실행파일 폴더. 모양이 예상과 다르면 None
// -out: error = 없음(판단 실패는 None)
//------------------------------------------------------------------
fn exe_dir_from_out_dir() -> Option<PathBuf> {
    let out_dir = std::env::var("OUT_DIR").ok()?;
    Path::new(&out_dir).ancestors().nth(3).map(|p| p.to_path_buf())
}

//------------------------------------------------------------------
// 이 대상 OS 에서 쓸 pdfium 파일 이름과 원본 후보들
//=> 배포본(dist-onedir) 안에 OS별로 정본이 들어 있다. 그것을 그대로 쓴다 —
//   빌드마다 다른 파일을 집어오면 배포본과 개발 빌드의 동작이 갈린다.
//
// -in: 없음(cargo 가 주는 CARGO_CFG_TARGET_OS 를 읽는다)
//
// -out: Option<(&'static str, Vec<PathBuf>)> = (파일명, 원본 후보 경로들)
//                                              지원하지 않는 OS 면 None
// -out: error = 없음
//------------------------------------------------------------------
fn lib_name_and_sources() -> Option<(&'static str, Vec<PathBuf>)> {
    // 크레이트 루트(= Rust/) 기준으로 찾는다. 빌드를 어느 폴더에서 걸든 같아진다.
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").ok()?);
    let os = std::env::var("CARGO_CFG_TARGET_OS").ok()?;

    let (name, rel): (&str, Vec<&str>) = match os.as_str() {
        "windows" => ("pdfium.dll", vec![
            "dist-onedir/windows/pdfium.dll",
            // 파이썬 배포본에도 같은 파일이 있다(sha256 동일) — Rust 배포본을
            // 아직 안 만든 작업 트리를 위한 2차 후보.
            "../dist/csoclassify/_internal/pypdfium2_raw/pdfium.dll",
        ]),
        "linux" => ("libpdfium.so", vec![
            "dist-onedir/linux/libpdfium.so",
            "../dist/csoclassify/_internal/pypdfium2_raw/libpdfium.so",
        ]),
        // macOS 용 정본은 배포본에 없다 — 있으면 그때 후보를 더한다.
        _ => return None,
    };
    Some((name, rel.into_iter().map(|r| root.join(r)).collect()))
}

//------------------------------------------------------------------
// 이미 같은 파일이 놓여 있나
//=> 매 빌드마다 7MB 를 다시 복사할 이유가 없다. 크기가 같으면 같은 파일로 본다
//   (정본이 바뀌면 크기도 바뀌고, 아래 rerun-if-changed 가 다시 돌린다).
//
// -in: src = 원본 경로
// -in: dst = 목적지 경로
//
// -out: bool = true 면 복사할 필요 없음
// -out: error = 없음(잴 수 없으면 false = 복사한다)
//------------------------------------------------------------------
fn already_same(src: &Path, dst: &Path) -> bool {
    match (std::fs::metadata(src), std::fs::metadata(dst)) {
        (Ok(a), Ok(b)) => a.len() == b.len(),
        _ => false,
    }
}

fn main() {
    let Some((name, sources)) = lib_name_and_sources() else {
        // 지원 목록에 없는 OS — 조용히 넘어간다(CSO_PDFIUM 으로 지정하면 된다).
        return;
    };

    // 정본이 바뀌면 이 스크립트를 다시 돌려 복사본을 갱신한다.
    for src in &sources {
        println!("cargo:rerun-if-changed={}", src.display());
    }
    // 환경변수로 직접 지정해 쓰는 사람도 있어, 그 값이 바뀌면 다시 판단하게 한다.
    println!("cargo:rerun-if-env-changed=CSO_PDFIUM");

    let Some(exe_dir) = exe_dir_from_out_dir() else {
        println!("cargo:warning=빌드 산출 폴더를 알 수 없어 {name} 복사를 건너뜁니다");
        return;
    };

    let Some(src) = sources.iter().find(|p| p.is_file()) else {
        // 배포본을 안 받은 작업 트리 — 빌드는 되게 두고, 왜 PDF 가 안 읽히는지만 알린다.
        println!("cargo:warning={name} 원본을 찾지 못했습니다 — PDF 추출이 비활성됩니다. \
                  CSO_PDFIUM 환경변수로 직접 지정하거나 배포본(dist-onedir)을 받으세요");
        return;
    };

    let dst = exe_dir.join(name);
    if already_same(src, &dst) {
        return;
    }

    if let Err(e) = std::fs::copy(src, &dst) {
        // 실행 중인 바이너리가 DLL 을 물고 있으면 윈도우에서 복사가 막힌다.
        // 이미 놓여 있던 파일이 그대로 쓰이므로 빌드를 깰 이유는 없다.
        println!("cargo:warning={name} 복사 실패({e}) — 이미 있는 파일을 그대로 씁니다");
    }
}

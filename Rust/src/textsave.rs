//! 추출 본문(.txt) 보존 — 설계: plan/추출텍스트-저장-설계-20260907.html
//! 파이썬 판 csoclassify/textsave.py 와 같은 규칙·같은 파일 모양을 낸다.

use std::io::Write;
use std::path::{Path, PathBuf};

/// 색인 파일 이름. '.txt' 가 아니어야 한다 — 받는 쪽이 폴더를 *.txt 로 훑을 때
/// 색인이 딸려 들어가면 안 된다.
pub const INDEX_NAME: &str = "_index.jsonl";

/// 폴더만 발견한 사람을 위한 안내문. 이것도 .txt 가 아니다(같은 이유).
pub const README_NAME: &str = "_README.md";

const README_TEXT: &str = "\
# 이 폴더에 무엇이 들어 있나

MpowerClassify 가 `--textsave` 로 남긴 **문서 본문 발췌**입니다.
파일 이름은 원본 파일의 SHA-256 이고, 내용은 분류할 때 실제로 읽은 텍스트입니다.

## 취급 주의

**원문이 그대로 들어 있습니다 — 개인정보·기밀이 포함될 수 있습니다.**
보안등급 C(기밀) 문서의 본문도 걸러내지 않고 그대로 남깁니다.
공유 폴더·백업·소스 저장소에 올리지 마세요.

## 어느 원본인지 찾으려면

`_index.jsonl` 을 보세요. 한 줄에 한 문서이며 `hash`·`txt`·`file` 로 이어집니다.
";

//------------------------------------------------------------------
// 저장 폴더 준비 (실행 시작 전 1회)
//=> 폴더를 만들고 '정말 쓸 수 있는지'까지 실제로 써 보며 확인한다.
//   문서 수천 건을 다 돌린 뒤에 "한 건도 저장 못 했다"를 알게 되면 안 되므로,
//   시작 전에 확인해서 안 되면 그 자리에서 끝낸다(설계 9장).
//    1) 폴더 생성(이미 있으면 그대로)
//    2) 임시 파일을 하나 써 보고 지운다 — 읽기전용 폴더도 create_dir_all 은 통과한다
//    3) 안내문(_README.md)을 없을 때만 만든다
//
// -in: save_dir = 저장 폴더 경로(상대경로면 절대경로로 바꿔 돌려준다)
//
// -out: Ok(PathBuf) = 절대경로로 정규화된 저장 폴더
// -out: error = 만들 수 없거나 쓸 수 없으면 Err(사람이 읽을 사유)
//------------------------------------------------------------------
pub fn prepare(save_dir: &str) -> Result<PathBuf, String> {
    // 데몬·다른 작업 폴더에서 상대경로를 그대로 쓰면 엉뚱한 데 쌓인다(설계 11장).
    let path = std::fs::canonicalize(save_dir)
        .map(|p| {
            // 윈도우 canonicalize 는 확장 길이 접두(\\?\C:\...)를 붙인다. 화면과
            // 색인에 그대로 나가면 파이썬 판과 경로 모양이 달라 보인다 — 떼어 낸다.
            let t = p.to_string_lossy().to_string();
            PathBuf::from(t.strip_prefix(r"\\?\").unwrap_or(&t).to_string())
        })
        .unwrap_or_else(|_| {
            std::env::current_dir()
                .map(|c| c.join(save_dir))
                .unwrap_or_else(|_| PathBuf::from(save_dir))
        });
    std::fs::create_dir_all(&path).map_err(|e| e.to_string())?;
    // '만들어졌다'와 '쓸 수 있다'는 다르다.
    let probe = path.join(".__write_test");
    std::fs::write(&probe, b"ok").map_err(|e| e.to_string())?;
    let _ = std::fs::remove_file(&probe);
    // 폴더만 발견한 제3자를 위한 경고. 이미 있으면 건드리지 않는다(사람이 고쳤을 수 있다).
    let readme = path.join(README_NAME);
    if !readme.exists() {
        std::fs::write(&readme, README_TEXT).map_err(|e| e.to_string())?;
    }
    Ok(path)
}

//------------------------------------------------------------------
// 본문 한 건 저장
//=> 파일 이름을 원본의 SHA-256 으로 삼는다. 그래서 내용이 같은 문서가 여러 경로에
//   있어도 .txt 는 한 개만 생기고(중복 제거), 결과 레코드의 hash 칸으로 바로 찾을 수 있다.
//
// -in: save_dir = prepare() 가 돌려준 폴더
// -in: sha      = 원본 파일의 SHA-256 (레코드의 hash 와 같은 값)
// -in: text     = 저장할 본문(정제·절단까지 끝난, 판정에 실제로 쓴 텍스트)
//
// -out: Ok((name, deduped)) = 저장된 파일 이름, 이미 있어서 안 썼으면 true
// -out: error = 쓰기 실패 시 Err(사유) — 부르는 쪽이 문서 한 건만 건너뛴다
//------------------------------------------------------------------
pub fn save(save_dir: &Path, sha: &str, text: &str) -> Result<(String, bool), String> {
    let name = format!("{}.txt", sha);
    let dest = save_dir.join(&name);
    if dest.exists() {
        // 같은 해시 = 같은 '원본 파일'. 그런데 저장하는 것은 원본이 아니라
        // *추출한 본문*이고, 파이썬 판과 이 판은 파서가 달라 같은 문서에서도
        // 본문이 다르게 나온다(실측: 같은 pptx 가 9,312 / 9,330 바이트).
        // 그러니 "이미 있으니 됐다"로 넘기면, 다른 엔진이 만든 남의 본문을
        // 내 것인 양 가리키게 된다 — 조용한 거짓말이다. 내용을 확인한다.
        match std::fs::read(&dest) {
            Ok(old) if old == text.as_bytes() => return Ok((name, true)),
            Ok(_) => return Err(
                "같은 해시의 다른 본문이 이미 있습니다 — 다른 엔진(파이썬 판)이나 \
                 다른 버전으로 만든 폴더로 보입니다. 폴더를 나누세요"
                    .into()),
            Err(e) => return Err(e.to_string()),
        }
    }
    // 바이트를 그대로 쓴다 — 개행을 손대지 않아야 파이썬 판과 파일이 같아진다.
    std::fs::write(&dest, text.as_bytes()).map_err(|e| e.to_string())?;
    Ok((name, false))
}

//------------------------------------------------------------------
// 색인 한 줄 덧붙이기
//=> hash 이름만으로는 사람이 어느 문서인지 못 알아본다. 그 다리를 놓는 파일이다.
//   덮어쓰지 않고 이어붙인다 — 어제 돌린 색인을 오늘 실행이 말없이 지우는 것이
//   더 위험하기 때문이다(설계 7장).
//
// -in: save_dir  = prepare() 가 돌려준 폴더
// -in: sha       = 원본 SHA-256
// -in: name      = 저장된 .txt 이름
// -in: file      = 원본 표시 경로(압축 내부면 '압축경로/내부경로')
// -in: doc_id    = 문서 식별자(sfile_id 로 얻은 것만, 아니면 None)
// -in: chars     = 저장한 글자 수(파이썬 len(text) 와 같게 '문자' 수로 센다)
// -in: truncated = G3 로 뒤가 잘린 본문인가
// -in: ts        = 저장 시각 문자열(레코드의 ts 와 같은 모양)
//
// -out: Ok(())
// -out: error = 쓰기 실패 시 Err(사유)
//------------------------------------------------------------------
#[allow(clippy::too_many_arguments)]
pub fn index_append(
    save_dir: &Path,
    sha: &str,
    name: &str,
    file: &str,
    doc_id: Option<&str>,
    chars: usize,
    truncated: bool,
    ts: &str,
) -> Result<(), String> {
    // 칸 이름·차례가 파이썬 판과 같아야 두 판의 색인을 한 파일에 섞어도 읽힌다.
    let row = serde_json::json!({
        "hash": sha, "txt": name, "file": file,
        "doc_id": doc_id, "chars": chars,
        "truncated": truncated, "ts": ts,
    });
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(save_dir.join(INDEX_NAME))
        .map_err(|e| e.to_string())?;
    writeln!(f, "{}", row).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    //------------------------------------------------------------------
    // 같은 내용은 한 파일로 합쳐진다
    //=> 이름이 내용 해시라, 같은 문서가 여러 경로에 있어도 .txt 는 하나여야 한다.
    //   이게 깨지면 중복 문서가 많은 실제 폴더에서 디스크가 몇 배로 든다.
    //------------------------------------------------------------------
    #[test]
    fn 같은_해시는_다시_쓰지_않는다() {
        let dir = std::env::temp_dir().join(format!("csoc_ts_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let p = prepare(dir.to_str().unwrap()).unwrap();

        let (n1, d1) = save(&p, "abc123", "본문").unwrap();
        let (n2, d2) = save(&p, "abc123", "본문").unwrap();
        assert_eq!(n1, "abc123.txt");
        assert_eq!(n1, n2);
        assert!(!d1, "처음엔 새로 써야 한다");
        assert!(d2, "두 번째는 이미 있으니 안 써야 한다");
        // 안내문은 자동으로 생긴다 — 폴더만 발견한 사람에게 경고가 닿아야 한다.
        assert!(p.join(README_NAME).exists());

        index_append(&p, "abc123", &n1, "D:/a.hwp", None, 2, false, "2026-09-07T00:00:00+09:00").unwrap();
        index_append(&p, "abc123", &n1, "D:/b.hwp", None, 2, false, "2026-09-07T00:00:00+09:00").unwrap();
        let idx = std::fs::read_to_string(p.join(INDEX_NAME)).unwrap();
        // .txt 는 하나지만 색인은 경로마다 한 줄 — "어느 경로에서 나왔나"를 잃지 않는다.
        assert_eq!(idx.lines().count(), 2, "{}", idx);

        let _ = std::fs::remove_dir_all(&dir);
    }
}

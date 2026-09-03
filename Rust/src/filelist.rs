//! 문서 식별자(doc_id) · 입력 목록(--filelist) 어댑터 — Python 판의 짝.
//!
//! 설계: plan/문서분류체계-연동-분류규칙-설계.html §7-5 · §7-5-2-1 · §7-5-3 (D0 · D0b)
//!
//! 이 모듈이 푸는 문제는 하나다 — "같은 문서인가"를 무엇으로 판단할 것인가.
//! 지금까지는 경로 문자열이 유일한 이름표였는데, 경로는 문서의 주소이지 신분증이
//! 아니다. 폴더를 옮기거나 이름만 바꿔도 값이 달라지고, 그러면 그 문서에 쌓아 둔
//! 사람의 판단 이력이 통째로 끊긴다. 실제로 저장소에서 드라이브 문자 대소문자
//! 차이(D: vs d:) 하나 때문에 사람이 고친 등급 4건이 조용히 반영되지 않고 있었다.
//!
//! [Python 과의 관계] `src/csoclassify/filelist.py` 와 **같은 값을 내야 한다**.
//! 두 판이 다른 doc_id 를 내면 같은 문서가 매핑 테이블에 두 건으로 들어간다.
//! `tests::python_판과_같은_정규화를_한다` 가 같은 입력 표로 그것을 지킨다.

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::Read;
use std::path::Path;

/// doc_id 폭. 문서분류체계 DB 의 DC_ID 가 VARCHAR(40) 이라 짝이 될 doc_id 도 같은
/// 폭으로 맞춘다. SHA-256 hex 는 64자라 앞 40자(160비트)만 쓴다 — 1억 문서 기준
/// 충돌 확률이 약 10⁻³³ 로 사실상 0 이고, SHA-1(정확히 40자)보다 충돌 내성이 좋다.
pub const DOC_ID_LEN: usize = 40;

//------------------------------------------------------------------
// 경로 → 정규화 키
//=> 같은 파일을 가리키는 서로 다른 표기를 한 모양으로 모은다.
//    1) 구분자를 '/' 로 통일     (D:\a\b  →  D:/a/b)
//    2) '..' · '.' 을 해소       (D:/a/../b  →  D:/b)
//    3) 드라이브 문자만 대문자   (d:/a  →  D:/a)
//
//   [왜 대소문자를 보존하나] 윈도우는 경로 대소문자를 구분하지 않고 리눅스는
//   구분한다. 정규화 규칙을 OS 별로 다르게 하면 같은 결과 파일이 OS 를 옮길 때
//   다르게 해석된다. 그래서 리눅스 기준(보존)으로 고정하고, 대소문자만 다른
//   경우는 매칭 3단계에서 한 번 더 시도하되 그 사실을 기록한다.
//
//   [왜 std 의 canonicalize 를 안 쓰나] 그건 파일이 실제로 있어야 하고 심볼릭
//   링크까지 따라간다. 우리는 '목록에 적힌 글자'와 '스캔한 글자'를 맞추는 것이지
//   디스크를 묻는 것이 아니다 — 없는 파일도 키를 가져야 한다(F4).
//------------------------------------------------------------------
pub fn normalize_key(path: &str) -> String {
    let s = path.trim().replace('\\', "/");
    if s.is_empty() {
        return String::new();
    }
    // UNC(//server/share)의 앞 슬래시 두 개는 의미가 있어 따로 떼어 둔다.
    let unc = s.starts_with("//");
    let body = if unc { &s[2..] } else { &s[..] };

    // '.' 은 버리고 '..' 은 한 칸 되돌린다. 절대경로 앞의 빈 조각은 살려 둬야
    // 선행 '/' 가 사라지지 않는다.
    let lead_slash = body.starts_with('/');
    let mut out: Vec<&str> = Vec::new();
    for part in body.split('/') {
        match part {
            "" | "." => continue,
            ".." => {
                // 되돌릴 것이 없으면(맨 앞의 '..') 그대로 둔다 — 지우면 다른 경로가 된다.
                if matches!(out.last(), Some(&p) if p != "..") {
                    out.pop();
                } else {
                    out.push(part);
                }
            }
            _ => out.push(part),
        }
    }
    let mut joined = out.join("/");
    if lead_slash {
        joined.insert(0, '/');
    }
    if unc {
        joined.insert_str(0, "//");
    }
    // 드라이브 문자(맨 앞 "x:")만 대문자로. 그 뒤 내용은 손대지 않는다.
    let b: Vec<char> = joined.chars().collect();
    if b.len() >= 2 && b[1] == ':' && b[0].is_ascii_alphabetic() {
        joined = format!("{}{}", b[0].to_ascii_uppercase(),
                         b[1..].iter().collect::<String>());
    }
    joined
}

//------------------------------------------------------------------
// 내용 해시로 doc_id 만들기 (폴백 ②)
//=> sfile_id 를 못 얻은 문서용. 파일 내용의 SHA-256 앞 40자를 쓴다.
//   문서를 옮기거나 이름을 바꿔도 유지되지만, 내용을 고치면 값이 바뀐다.
//   그래서 이 값은 매핑 테이블에 넣지 않는다 — doc_id_source 로 구분한다.
//------------------------------------------------------------------
pub fn content_doc_id(path: &Path) -> Option<String> {
    let mut f = std::fs::File::open(path).ok()?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];      // 1MB 씩 — 대용량에서 메모리가 튀지 않게
    loop {
        let n = f.read(&mut buf).ok()?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Some(hex40(&h.finalize()))
}

//------------------------------------------------------------------
// 경로 해시로 doc_id 만들기 (폴백 ③)
//=> 내용조차 못 읽는 파일(암호 걸린 zip·손상 파일)의 최후 수단.
//------------------------------------------------------------------
pub fn path_doc_id(key: &str) -> String {
    let mut h = Sha256::new();
    h.update(key.as_bytes());
    hex40(&h.finalize())
}

/// 해시 바이트 → 앞 40 hex 자.
fn hex40(digest: &[u8]) -> String {
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{:02x}", b));
    }
    s.truncate(DOC_ID_LEN);
    s
}

//------------------------------------------------------------------
// 목록 한 줄 — 경로와 그 문서의 sfile_id
//------------------------------------------------------------------
#[derive(Clone, Debug)]
pub struct Entry {
    pub path: String,
    pub sfile_id: String,
    pub hash: Option<String>,
}

//------------------------------------------------------------------
// 읽어 들인 입력 목록 — 경로로 sfile_id 를 찾는 사전
//=> 목록은 '대상 지정'과 'ID 사전' 두 가지로 쓰인다. 어느 쪽이든 조회는 정규화
//   키로 하고, 못 찾으면 대소문자를 접어 한 번 더 본다.
//------------------------------------------------------------------
#[derive(Debug)]
pub struct FileList {
    pub entries: HashMap<String, Entry>,
    pub warnings: Vec<String>,
    pub lines: usize,
    pub bad_lines: usize,
    pub no_id: usize,
    pub missing_file: usize,
    pub dup_sfile_id: usize,
    folded: HashMap<String, String>,
}

impl FileList {
    //--------------------------------------------------------------
    // 경로로 목록 항목 찾기
    //=> ① 정규화 키가 같으면 그것, ② 대소문자만 다르면 그것(구제).
    //--------------------------------------------------------------
    pub fn lookup(&self, path: &str) -> Option<(&Entry, &'static str)> {
        let key = normalize_key(path);
        if let Some(e) = self.entries.get(&key) {
            return Some((e, "key"));
        }
        let k2 = self.folded.get(&key.to_lowercase())?;
        self.entries.get(k2).map(|e| (e, "case"))
    }

    //--------------------------------------------------------------
    // 목록이 정한 처리 대상 경로들
    //=> --filelist 를 단독으로 준 경우에 쓴다. 실제로 있는 파일만 돌려준다
    //   (없는 파일은 F4 로 이미 경고했다).
    //--------------------------------------------------------------
    pub fn target_paths(&self) -> Vec<String> {
        let mut v: Vec<String> = self.entries.values()
            .filter(|e| Path::new(&e.path).is_file())
            .map(|e| e.path.clone())
            .collect();
        v.sort();
        v
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

//------------------------------------------------------------------
// 목록 파일 읽기 (jsonl · csv) — 핵심
//=> MpowerV11 이 뽑아 준 {경로, sfile_id} 목록을 읽어 색인을 만든다.
//    1) 첫 줄이 '{' 이거나 확장자가 json 계열이면 jsonl, 아니면 csv
//    2) 한 줄씩 해석하며 F1(파싱 실패)·F2(ID 없음)를 세어 건너뛴다
//    3) 같은 정규화 키에 다른 ID 가 오면 F3 으로 즉시 멈춘다
//    4) 다 읽은 뒤 F4(파일 없음)·F7(한 ID 가 여러 경로)을 경고로 모은다
//
//   [왜 F3 만 멈추나] 한 파일에 두 ID 는 모순이라 어느 쪽을 골라도 틀린다.
//   나머지는 "이만큼은 ID 를 못 얻었다"고 세어서 알려 주면 충분하다.
//------------------------------------------------------------------
pub fn load(path: &str) -> Result<FileList, String> {
    let p = Path::new(path);
    if !p.is_file() {
        return Err(format!("목록 파일을 찾을 수 없습니다: {}", path));
    }
    let raw = std::fs::read(p)
        .map_err(|e| format!("목록 파일을 읽을 수 없습니다: {} ({})", path, e))?;
    // BOM 을 떼고 읽는다 — 안 떼면 첫 열 이름이 '\u{feff}path' 가 된다.
    let text = String::from_utf8_lossy(&raw);
    let text = text.strip_prefix('\u{feff}').unwrap_or(&text).to_string();

    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    if lines.is_empty() {
        return Err(format!("목록 파일이 비어 있습니다: {}", path));
    }
    let base = p.parent().map(|d| d.to_path_buf()).unwrap_or_default();
    let fname = p.file_name().map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| path.to_string());

    let (rows, mut bad) = parse_rows(&lines, path)?;

    let mut entries: HashMap<String, Entry> = HashMap::new();
    let mut seen_id: HashMap<String, Vec<String>> = HashMap::new();
    let mut no_id = 0usize;

    for (lineno, row) in rows {
        let raw_path = row.get("path").map(|s| s.trim()).unwrap_or("");
        if raw_path.is_empty() {
            bad += 1;
            continue;
        }
        let sfile_id = row.get("sfile_id").map(|s| s.trim()).unwrap_or("");
        // F2 — sfile_id 가 비었거나 없으면 그 줄은 없는 것으로 본다.
        if sfile_id.is_empty() {
            no_id += 1;
            continue;
        }
        // 상대경로는 '현재 작업폴더'가 아니라 '목록 파일이 있는 폴더' 기준으로 푼다.
        // 목록과 문서를 함께 다른 곳으로 옮겨도 깨지지 않게 하려는 것이다.
        let abs = if Path::new(raw_path).is_absolute()
            || raw_path.starts_with("//") || raw_path.starts_with("\\\\") {
            raw_path.to_string()
        } else {
            base.join(raw_path).to_string_lossy().into_owned()
        };
        let key = normalize_key(&abs);
        // F3 — 같은 파일에 다른 ID. 어느 쪽을 골라도 틀리므로 멈춘다.
        if let Some(prev) = entries.get(&key) {
            if prev.sfile_id != sfile_id {
                return Err(format!(
                    "목록 {} {}번째 줄 — 같은 파일에 sfile_id 가 두 개입니다(F3).\n  \
                     경로: {}\n  먼저: {}\n  나중: {}\n  \
                     어느 쪽을 골라도 틀리므로 분류를 시작하지 않았습니다. \
                     목록을 고쳐 다시 실행하세요.",
                    fname, lineno, key, prev.sfile_id, sfile_id));
            }
        } else {
            let h = row.get("hash").map(|s| s.trim()).filter(|s| !s.is_empty())
                .map(|s| s.to_string());
            entries.insert(key.clone(), Entry {
                path: abs, sfile_id: sfile_id.to_string(), hash: h,
            });
        }
        seen_id.entry(sfile_id.to_string()).or_default().push(key);
    }

    // F1 — 한 줄도 못 읽었으면 데이터가 아니라 형식을 잘못 준 것이다.
    if entries.is_empty() {
        return Err(format!(
            "목록 {} — 쓸 수 있는 줄이 하나도 없습니다(전체 {}줄 · 파싱 실패 {} · \
             sfile_id 없음 {}).\n  jsonl 이면 {{\"path\": ..., \"sfile_id\": ...}} \
             한 줄씩, csv 면 첫 줄에 path,sfile_id 헤더가 필요합니다.",
            fname, lines.len(), bad, no_id));
    }

    let mut warnings = Vec::new();
    // F4 — 목록에 있는데 파일이 없다. 목록이 낡았을 수 있다(경고만).
    let missing: Vec<&Entry> = entries.values()
        .filter(|e| !Path::new(&e.path).is_file()).collect();
    let missing_file = missing.len();
    if missing_file > 0 {
        warnings.push(format!(
            "[F4] 목록에 있으나 파일이 없습니다: {}건 (예: {}) — 목록이 낡았을 수 있습니다",
            missing_file, missing[0].path));
    }
    // F7 — 같은 ID 가 여러 경로에. 사본이면 정상이라 경고만 한다.
    let dups: Vec<(&String, &Vec<String>)> =
        seen_id.iter().filter(|(_, v)| v.len() > 1).collect();
    let dup_sfile_id = dups.len();
    if dup_sfile_id > 0 {
        warnings.push(format!(
            "[F7] 같은 sfile_id 가 여러 경로에 있습니다: {}건 (예: {} → {}곳) — \
             같은 문서의 사본이면 정상입니다",
            dup_sfile_id, dups[0].0, dups[0].1.len()));
    }
    if bad > 0 {
        warnings.push(format!("[F1] 해석하지 못한 줄 {}건을 건너뛰었습니다", bad));
    }
    if no_id > 0 {
        warnings.push(format!("[F2] sfile_id 가 비어 있는 줄 {}건을 건너뛰었습니다", no_id));
    }

    // 대소문자만 다른 표기를 구제하기 위한 보조 색인. 같은 접힘 키에 둘 이상이
    // 걸리면 어느 쪽인지 알 수 없으므로 그 키는 색인에서 뺀다(조용한 오연결 방지).
    let mut fold: HashMap<String, Vec<String>> = HashMap::new();
    for k in entries.keys() {
        fold.entry(k.to_lowercase()).or_default().push(k.clone());
    }
    let folded = fold.into_iter()
        .filter(|(_, v)| v.len() == 1)
        .map(|(f, v)| (f, v[0].clone()))
        .collect();

    Ok(FileList {
        lines: lines.len(),
        bad_lines: bad,
        no_id,
        missing_file,
        dup_sfile_id,
        entries,
        warnings,
        folded,
    })
}

//------------------------------------------------------------------
// 본문 줄들 → (줄번호, 필드맵) 목록
//=> jsonl 이냐 csv 냐를 가려서 해석한다. 판정은 첫 줄이 '{' 로 시작하는지를
//   먼저 보고, 아니면 확장자를 본다 — 확장자를 잘못 붙여 온 목록 때문에
//   통째로 실패하는 것보다 낫다.
//------------------------------------------------------------------
fn parse_rows(lines: &[&str], path: &str)
    -> Result<(Vec<(usize, HashMap<String, String>)>, usize), String>
{
    let ext = Path::new(path).extension()
        .map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    let is_json = lines[0].trim_start().starts_with('{')
        || matches!(ext.as_str(), "jsonl" | "json" | "ndjson");

    let mut rows = Vec::new();
    let mut bad = 0usize;
    if is_json {
        for (i, ln) in lines.iter().enumerate() {
            match serde_json::from_str::<serde_json::Value>(ln) {
                Ok(v) => match v.as_object() {
                    Some(o) => {
                        let mut m = HashMap::new();
                        for (k, val) in o {
                            // 값은 문자열만 쓴다. 숫자로 온 id 도 글자로 받아 준다.
                            let s = match val {
                                serde_json::Value::String(s) => s.clone(),
                                serde_json::Value::Null => String::new(),
                                other => other.to_string(),
                            };
                            m.insert(k.clone(), s);
                        }
                        rows.push((i + 1, m));
                    }
                    None => bad += 1,
                },
                Err(_) => bad += 1,      // F1 — 그 줄만 버리고 계속 간다
            }
        }
        return Ok((rows, bad));
    }

    // csv/tsv — 첫 줄이 헤더다. 탭이 콤마보다 많으면 탭 구분으로 본다.
    let delim = if lines[0].matches('\t').count() > lines[0].matches(',').count() {
        '\t'
    } else {
        ','
    };
    let header: Vec<String> = split_csv(lines[0], delim)
        .into_iter().map(|s| s.trim().to_string()).collect();
    if !header.iter().any(|h| h == "path") || !header.iter().any(|h| h == "sfile_id") {
        let shown = if header.is_empty() { "(헤더 없음)".to_string() } else { header.join(", ") };
        return Err(format!(
            "목록 {} — csv 첫 줄에 path · sfile_id 열이 필요합니다(지금: {})",
            Path::new(path).file_name().map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.to_string()),
            shown));
    }
    for (i, ln) in lines.iter().enumerate().skip(1) {
        let cells = split_csv(ln, delim);
        let mut m = HashMap::new();
        for (h, c) in header.iter().zip(cells.into_iter()) {
            m.insert(h.clone(), c);
        }
        rows.push((i + 1, m));
    }
    Ok((rows, bad))
}

/// csv 한 줄 나누기 — 큰따옴표로 감싼 칸과 그 안의 "" 이스케이프를 지킨다.
/// (경로에 콤마가 들어가면 따옴표로 감싸 오므로 최소한 그건 받아 줘야 한다.)
fn split_csv(line: &str, delim: char) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quoted = false;
    let mut it = line.chars().peekable();
    while let Some(c) = it.next() {
        if quoted {
            if c == '"' {
                if it.peek() == Some(&'"') {
                    it.next();
                    cur.push('"');           // "" → 따옴표 한 개
                } else {
                    quoted = false;
                }
            } else {
                cur.push(c);
            }
        } else if c == '"' {
            quoted = true;
        } else if c == delim {
            out.push(std::mem::take(&mut cur));
        } else {
            cur.push(c);
        }
    }
    out.push(cur);
    out
}

//------------------------------------------------------------------
// 문서 하나의 doc_id 정하기 — 우선순위 3단계
//=> ① 목록의 sfile_id → ② 내용 해시 → ③ 경로 해시 순으로 시도한다.
//   어느 것으로 채웠는지를 함께 돌려주어, 나중에 매핑 테이블에 넣을 때 ①만
//   골라 넣을 수 있게 한다(②·③은 MpowerV11 문서와 이어지지 않는다).
//
// -out: (doc_id, source, key, matched)
//         source  = "sfile_id" | "content" | "path"
//         matched = 목록과 이어진 방법("key"|"case") · 못 이었으면 None
//------------------------------------------------------------------
pub fn resolve_doc_id(display: &str, read_path: &Path, flist: Option<&FileList>)
    -> (String, &'static str, String, Option<&'static str>)
{
    let key = normalize_key(display);
    if let Some(fl) = flist {
        if let Some((e, how)) = fl.lookup(display) {
            return (e.sfile_id.clone(), "sfile_id", key, Some(how));
        }
    }
    // ② 내용 해시 — 옮기거나 이름을 바꿔도 유지된다.
    if let Some(cid) = content_doc_id(read_path) {
        return (cid, "content", key, None);
    }
    // ③ 내용조차 못 읽는 파일(암호 zip·손상)의 최후 수단.
    let pid = path_doc_id(&key);
    (pid, "path", key, None)
}

#[cfg(test)]
mod tests {
    use super::*;

    //--------------------------------------------------------------
    // [중요] Python 판과 같은 정규화를 한다
    //=> 두 판이 다른 doc_id/key 를 내면 같은 문서가 매핑 테이블에 두 건으로
    //   들어가고, 화면이 결과를 못 잇는다. 아래 표는
    //   tests/test_docid.py::test_normalize_key_absorbs_spelling 과 같은 값이다.
    //   한쪽을 고치면 다른 쪽 시험이 빨간불이 되어야 한다.
    //--------------------------------------------------------------
    #[test]
    fn python_판과_같은_정규화를_한다() {
        let canon = "D:/collected/HWP/a.hwp";
        assert_eq!(normalize_key(r"d:\collected\HWP\a.hwp"), canon);
        assert_eq!(normalize_key("D:/collected/HWP/a.hwp"), canon);
        assert_eq!(normalize_key(r"D:\collected\x\..\HWP\a.hwp"), canon);
        // 대소문자는 보존한다(리눅스는 경로 대소문자를 구분한다).
        assert_ne!(normalize_key("D:/collected/hwp/a.hwp"), canon);
        // UNC 의 앞 슬래시 두 개는 지킨다.
        assert_eq!(normalize_key(r"\\srv\share\a.hwp"), "//srv/share/a.hwp");
        assert_eq!(normalize_key("//srv/share/a.hwp"), "//srv/share/a.hwp");
        // 절대경로의 선행 슬래시가 사라지면 다른 경로가 된다.
        assert_eq!(normalize_key("/srv/docs/./a.txt"), "/srv/docs/a.txt");
        assert_eq!(normalize_key(""), "");
    }

    #[test]
    fn doc_id_는_40자다() {
        assert_eq!(path_doc_id("D:/a.txt").len(), DOC_ID_LEN);
    }

    #[test]
    fn f3_같은_파일에_다른_id면_멈춘다() {
        let d = std::env::temp_dir().join("csoc_fl_f3");
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("l.jsonl");
        std::fs::write(&p,
            "{\"path\": \"D:/a.txt\", \"sfile_id\": \"AAA\"}\n\
             {\"path\": \"d:\\\\a.txt\", \"sfile_id\": \"BBB\"}\n").unwrap();
        let e = load(p.to_str().unwrap()).unwrap_err();
        assert!(e.contains("F3"), "F3 로 멈춰야 한다: {}", e);
    }

    #[test]
    fn f1_f2_는_세고_전부_깨지면_멈춘다() {
        let d = std::env::temp_dir().join("csoc_fl_f12");
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("g.jsonl");
        std::fs::write(&p,
            "이건 JSON 이 아니다\n\
             {\"path\": \"D:/a.txt\", \"sfile_id\": \"\"}\n\
             {\"path\": \"D:/b.txt\", \"sfile_id\": \"SF-B\"}\n").unwrap();
        let fl = load(p.to_str().unwrap()).unwrap();
        assert_eq!((fl.bad_lines, fl.no_id, fl.len()), (1, 1, 1));

        let p2 = d.join("b.jsonl");
        std::fs::write(&p2, "아무것도\n아니다\n").unwrap();
        assert!(load(p2.to_str().unwrap()).is_err());
    }

    #[test]
    fn 대소문자만_다르면_구제한다() {
        let d = std::env::temp_dir().join("csoc_fl_case");
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("l.jsonl");
        std::fs::write(&p, "{\"path\": \"D:/collected/HWP/a.hwp\", \"sfile_id\": \"SF-1\"}\n")
            .unwrap();
        let fl = load(p.to_str().unwrap()).unwrap();
        // 정규화가 흡수하는 차이(드라이브 문자·구분자)는 "key" 로 이어진다.
        let (e, how) = fl.lookup(r"d:\collected\HWP\a.hwp").unwrap();
        assert_eq!((e.sfile_id.as_str(), how), ("SF-1", "key"));
        // 폴더 이름의 대소문자는 정규화가 보존하므로 "case" 로 구제된다.
        let (e2, how2) = fl.lookup("D:/COLLECTED/hwp/a.hwp").unwrap();
        assert_eq!((e2.sfile_id.as_str(), how2), ("SF-1", "case"));
    }

    #[test]
    fn csv_도_읽는다() {
        let d = std::env::temp_dir().join("csoc_fl_csv");
        std::fs::create_dir_all(&d).unwrap();
        let p = d.join("l.csv");
        std::fs::write(&p, "path,sfile_id,memo\nD:/a.txt,SF-A,무시됨\n").unwrap();
        let fl = load(p.to_str().unwrap()).unwrap();
        assert_eq!(fl.lookup("D:/a.txt").unwrap().0.sfile_id, "SF-A");
    }
}

//! 축별 충돌 해소 전략 — doctype 축 몫 (Python classify/conflict.py 포팅).
//! security 축의 max 전략은 rules::max_grade 로 이미 있으므로 여기서 다루지
//! 않는다 — "여러 후보 → 채택 결과"라는 계산은 축마다 다르고(설계서 6장),
//! doctype 축(서열 없는 트리)은 "1등 하나"가 아니라 "임계값 넘는 후보를 전부"
//! 채택하는 것이 본질이라 별도 전략 함수가 필요하다.

use crate::doc_rules::ConflictSpec;
use crate::doctype::Candidate;

/// all/top_n 전략 — 임계값(min_confidence)을 넘는 후보를 신뢰도 내림차순으로
/// 고른다. n=None 이면 all(전부), n=Some(k) 면 top_n(상위 k 개만, 잘려나간
/// 개수를 두 번째 반환값으로 알린다 — 설계서 6-3 note "잘라낸 것은 반드시 알린다").
pub fn resolve_multi(candidates: Vec<Candidate>, n: Option<usize>, min_confidence: f64) -> (Vec<Candidate>, usize) {
    let mut kept: Vec<Candidate> = candidates.into_iter().filter(|c| c.confidence >= min_confidence).collect();
    // 신뢰도는 내림차순, 동점이면 dc_id 오름차순(재설계 8-5). 예전에는 입력
    // 순서에 맡겼는데 doctype 후보는 HashSet 순회로 만들어져 순서 자체가
    // 실행마다 달랐다 — top_n 을 켜면 "같은 문서를 두 번 돌리면 결과가 다름"이
    // 된다. 재현성은 우연에 맡길 수 없으므로 2차 키를 명시한다.
    kept.sort_by(|a, b| b.confidence.partial_cmp(&a.confidence)
        .unwrap_or(std::cmp::Ordering::Equal)
        .then_with(|| a.dc_id.cmp(&b.dc_id)));
    match n {
        None => (kept, 0),
        Some(k) => {
            let truncated = kept.len().saturating_sub(k);
            kept.truncate(k);
            (kept, truncated)
        }
    }
}

/// doc_rule.yaml 의 conflict: 설정으로 resolve_multi 를 실행하는 다리 역할.
pub fn resolve_doctype(candidates: Vec<Candidate>, conflict: &ConflictSpec) -> (Vec<Candidate>, usize) {
    let min_confidence = conflict.min_confidence.unwrap_or(0.0);
    let n = if conflict.strategy == "top_n" {
        conflict.n.map(|v| v.max(0) as usize)
    } else {
        None
    };
    resolve_multi(candidates, n, min_confidence)
}

/// 실행 시에도 전략을 바꿀 수 없는 축(T11). security 는 서열이 있어 안전측(max)이
/// 유일하게 정당한 선택이라, --conflict 로 약화되는 경로를 아예 막는다(설계서 R4).
pub const FIXED_STRATEGY_AXES: [&str; 1] = ["security"];

/// --conflict <axis>=<strategy> 가 실제로 전략을 바꾸기 전에 부른다.
/// 조용히 무시하지 않고 오류로 막는 것이 핵심이다 — "보안 등급이 실수로 약해지는"
/// 상황을 검증 단계에서 원천 차단한다.
pub fn ensure_overridable_axis(axis: &str) -> Result<(), String> {
    if FIXED_STRATEGY_AXES.contains(&axis) {
        return Err(format!(
            "'{}' 축은 전략을 실행 시에도 바꿀 수 없습니다(설계 T11) — \
             이 축은 서열이 있어 안전측(max)이 유일하게 정당한 선택입니다.", axis));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(dc_id: &str, confidence: f64) -> Candidate {
        Candidate::bare(dc_id.into(), dc_id.into(), vec![dc_id.into()], confidence, vec![])
    }

    #[test]
    fn all은_전부_내림차순() {
        let (kept, truncated) = resolve_multi(vec![cand("A", 0.7), cand("B", 0.85), cand("C", 0.6)], None, 0.0);
        assert_eq!(kept.iter().map(|c| c.dc_id.as_str()).collect::<Vec<_>>(), vec!["B", "A", "C"]);
        assert_eq!(truncated, 0);
    }

    #[test]
    fn min_confidence_미만은_제외() {
        let (kept, _) = resolve_multi(vec![cand("A", 0.7), cand("B", 0.3)], None, 0.5);
        assert_eq!(kept.iter().map(|c| c.dc_id.as_str()).collect::<Vec<_>>(), vec!["A"]);
    }

    #[test]
    fn top_n은_잘라내고_truncated_보고() {
        let candidates: Vec<Candidate> = (0..7).map(|i| cand(&i.to_string(), i as f64 / 10.0)).collect();
        let (kept, truncated) = resolve_multi(candidates, Some(3), 0.0);
        assert_eq!(kept.len(), 3);
        assert_eq!(truncated, 4);
        assert_eq!(kept.iter().map(|c| c.dc_id.as_str()).collect::<Vec<_>>(), vec!["6", "5", "4"]);
    }

    #[test]
    fn security축_전략_덮어쓰기는_막힌다() {
        assert!(ensure_overridable_axis("security").is_err());
        assert!(ensure_overridable_axis("doctype").is_ok());
    }

    #[test]
    fn resolve_doctype_top_n_전략() {
        let candidates: Vec<Candidate> = (0..5).map(|i| cand(&i.to_string(), i as f64 / 10.0)).collect();
        let conflict = ConflictSpec { strategy: "top_n".into(), n: Some(2), min_confidence: Some(0.25) };
        let (kept, truncated) = resolve_doctype(candidates, &conflict);
        assert_eq!(kept.iter().map(|c| c.dc_id.as_str()).collect::<Vec<_>>(), vec!["4", "3"]);
        assert_eq!(truncated, 0);
    }
}

import { useState, type FormEvent } from 'react'
import { post } from './api'
import M2Panel from './M2Panel'

const MAX_ROUNDS = 3

type Requirement = {
  project_id: string
  id: string
  category: string
  description: string
  status: 'confirmed' | 'needs_clarification' | 'proposed'
  source: string
  blocking: boolean
  acceptance_criteria: string[]
}

type Gap = {
  id: string
  category: string
  description: string
  blocking: boolean
  related_requirement_ids: string[]
}

// Review에서 Requirement와 연결된 Gap (결정 입력·추천 연결)
type GapLink = { gap: Gap; requirement_id: string }
type ProposalConversion = GapLink & { reason: 'round_limit'; round: number }

type Analysis = {
  project_id: string
  run_id: string
  clarification_round: number
  requirements: Requirement[]
  gaps: Gap[]
  clarification_needed: boolean
  analysis_mode: 'mock' | 'snowchat' | 'claude'
  usage: Record<string, unknown>[]
}

type ClarificationQuestion = {
  gap_id: string
  question: string
  ai_proposal: string // 질문 단계에서는 표시하지 않는다
}

type Answer = { gap_id: string; answer: string }

type ClarificationRound = {
  round: number
  gaps: Gap[]
  questions: ClarificationQuestion[]
  answers: Answer[]
}

export type WorkflowState = Analysis & {
  text: string
  phase: 'clarifying' | 'review' | 'approved'
  questions: ClarificationQuestion[]
  history: ClarificationRound[]
  edit_count: number
  gap_links: GapLink[]
  proposal_conversions?: ProposalConversion[]
}

// backend RequirementCategory와 같은 값
const CATEGORIES = ['role', 'functional', 'flow', 'permission', 'state', 'business_rule', 'exception', 'scope', 'integration', 'nfr']
// source는 내용의 출처다. 수락해도 바뀌지 않으므로 Confirmed의 'AI 제안' = AI가 제안하고 사용자가 수락한 항목
const SOURCE_LABELS: Record<string, string> = {
  initial_input: '최초 입력', clarification_answer: '질문 답변', ai_proposal: 'AI 제안', review_input: 'Review 추가',
}
const MODE_LABELS = {
  mock: 'Mock 분석 결과 · 규칙 기반 시연', snowchat: 'SnowChat LLM 분석 결과', claude: 'Claude Sonnet 분석 결과',
} as const
const GROUPS = [
  ['Confirmed Requirements', 'confirmed'],
  ['Needs Clarification', 'needs_clarification'],
  ['AI Proposals', 'proposed'],
] as const

type ReviewAction =
  | { type: 'accept'; ids: string[] }
  | { type: 'edit'; id: string; description: string; category: string; acceptance_criteria: string[]; confirm: boolean }
  | { type: 'add'; category: string; description: string; acceptance_criteria: string[] }
  | { type: 'resolve_gap'; gap_id: string; category?: string; description: string; acceptance_criteria: string[] }
  | { type: 'link_gap'; gap_ids: string[] }
  | { type: 'unlink_gap'; gap_id: string }
  | { type: 'approve' }

type ReviewedRequirements = {
  project_id: string
  run_id: string
  requirements: Requirement[]
  open_gaps: Gap[]
  gap_links: GapLink[]
  proposal_conversions?: ProposalConversion[]
  metrics: Record<string, number>
}

type PRDItem = { id: string; description: string; source_requirement_ids: string[]; source_gap_ids?: string[] }

type PRD = {
  project_id: string
  run_id: string
  overview: { summary: string; problem: string; goals: string[] }
  in_scope: PRDItem[]
  out_of_scope: PRDItem[]
  roles: PRDItem[]
  user_flows: PRDItem[]
  functional_requirements: PRDItem[]
  business_rules: PRDItem[]
  nfrs: PRDItem[]
  acceptance_criteria: (PRDItem & { target_id: string })[]
  assumptions: PRDItem[]
  open_issues: PRDItem[]
}

type PRDResult = { prd: PRD; validation: { passed: boolean; errors: string[]; warnings: string[] } }

const PRD_SECTIONS = [
  ['2. Scope (In)', 'in_scope'], ['2. Scope (Out)', 'out_of_scope'], ['3. Users / Roles', 'roles'],
  ['4. User Scenario / Flow', 'user_flows'], ['5. Functional Requirements', 'functional_requirements'],
  ['6. Business Rules', 'business_rules'], ['7. Non-functional Requirements', 'nfrs'],
  ['8. Acceptance Criteria', 'acceptance_criteria'], ['9. Assumptions', 'assumptions'], ['10. Open Issues', 'open_issues'],
] as const

// 수정·추가 폼 하나만 연다. id가 ''이면 새 Requirement 추가, gapId가 있으면 남은 Gap의 결정 입력
// (category ''는 서버가 Gap 분류에서 정한다)
type Draft = { id: string; category: string; description: string; ac: string; confirm: boolean; gapId?: string }

export default function App() {
  const [text, setText] = useState('')
  const [state, setState] = useState<WorkflowState | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [reviewed, setReviewed] = useState<ReviewedRequirements | null>(null)
  const [prdResult, setPrdResult] = useState<PRDResult | null>(null)
  const [approvedFrom, setApprovedFrom] = useState<WorkflowState | null>(null)

  // 질문 대기 중이면 답변 라운드, 아니면 분석 직후 첫 호출(answers 없이)
  async function step(current: WorkflowState) {
    const waiting = current.questions.length > 0
    setLoading(waiting ? '답변 반영 및 재분석 중… (약 30초)' : '확인 질문 생성 중… (약 30초)')
    setError('')
    try {
      const body = waiting
        ? { state: current, answers: current.questions.map(q => ({ gap_id: q.gap_id, answer: answers[q.gap_id] ?? '' })) }
        : { state: current }
      setState(await post<WorkflowState>('/api/clarification/step', body))
      setAnswers({})
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Clarification 요청에 실패했습니다.')
    } finally {
      setLoading('')
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!text.trim()) {
      setError('요구사항을 입력해주세요.')
      return
    }
    setLoading('요구사항 분석 중… (최대 1분)')
    setError('')
    let analysis: Analysis
    try {
      analysis = await post<Analysis>('/api/requirements/analyze', { text })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '분석 요청에 실패했습니다.')
      setLoading('')
      return
    }
    const initial: WorkflowState = { ...analysis, text, phase: 'clarifying', questions: [], history: [], edit_count: 0, gap_links: [] }
    setState(initial)
    await step(initial)
  }

  async function review(action: ReviewAction) {
    if (!state) return
    setLoading('처리 중…')
    setError('')
    try {
      const result = await post<{ state: WorkflowState; reviewed: ReviewedRequirements | null }>('/api/review', { state, action })
      setState(result.state)
      setReviewed(result.reviewed)
      if (action.type === 'approve') setApprovedFrom(state)
      setDraft(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Review 요청에 실패했습니다.')
    } finally {
      setLoading('')
    }
  }

  async function generatePrd() {
    if (!state || !reviewed) return
    setLoading('PRD 생성 중…')
    setError('')
    try {
      setPrdResult(await post<PRDResult>('/api/prd', { text: state.text, reviewed }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'PRD 생성에 실패했습니다.')
    } finally {
      setLoading('')
    }
  }

  function backToReview() {
    if (!approvedFrom) return
    setState(approvedFrom)
    setReviewed(null)
    setPrdResult(null)
    setApprovedFrom(null)
  }

  function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft) return
    const description = draft.description.trim()
    if (!description) {
      setError('내용을 입력해주세요.')
      return
    }
    const fields = { category: draft.category, description, acceptance_criteria: draft.ac.split('\n').map(line => line.trim()).filter(Boolean) }
    if (draft.gapId) review({ type: 'resolve_gap', gap_id: draft.gapId, ...fields, category: draft.category || undefined })
    else review(draft.id ? { type: 'edit', id: draft.id, ...fields, confirm: draft.confirm } : { type: 'add', ...fields })
  }

  function reset() {
    setState(null)
    setAnswers({})
    setError('')
    setDraft(null)
    setReviewed(null)
    setPrdResult(null)
    setApprovedFrom(null)
  }

  const editable = state?.phase === 'review' && !loading
  const pending = state?.requirements.filter(r => r.status === 'needs_clarification' && r.blocking) ?? []
  const proposals = state?.requirements.filter(r => r.status === 'proposed') ?? []
  // backend workflow.open_gaps·suggested_requirement_id와 같은 규칙: 남은 non-blocking Gap 전부, 관련 Requirement가 하나면 연결 추천
  const openGaps = state?.gaps.filter(g => !g.blocking) ?? []
  const suggestionFor = (gap: Gap) =>
    gap.related_requirement_ids.length === 1 ? state?.requirements.find(r => r.id === gap.related_requirement_ids[0]) : undefined
  const suggested = openGaps.filter(gap => suggestionFor(gap))

  const draftForm = draft && (
    <form onSubmit={saveDraft}>
      <label>category{' '}
        <select value={draft.category} onChange={event => setDraft({ ...draft, category: event.target.value })}>
          {draft.gapId && <option value="">Gap 분류 기준</option>}
          {CATEGORIES.map(category => <option key={category}>{category}</option>)}
        </select>
      </label>
      <label>내용 <textarea rows={2} value={draft.description} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      <label>Acceptance Criteria (한 줄에 하나)
        <textarea rows={3} value={draft.ac} onChange={event => setDraft({ ...draft, ac: event.target.value })} />
      </label>
      {draft.id && state?.requirements.find(r => r.id === draft.id)?.status !== 'confirmed' && (
        <label><input type="checkbox" checked={draft.confirm} onChange={event => setDraft({ ...draft, confirm: event.target.checked })} /> 확정 (confirmed로 변경)</label>
      )}
      <button key="save" disabled={!!loading}>저장</button>
      <button key="cancel" type="button" onClick={() => setDraft(null)} disabled={!!loading}>취소</button>
    </form>
  )

  function reviewItem(r: Requirement) {
    return (
      <li key={r.id}>
        <strong>{r.id}</strong> [{r.category}]{' '}
        <span className={r.source === 'ai_proposal' ? 'badge ai' : 'badge'}>{SOURCE_LABELS[r.source] ?? r.source}</span>
        {r.blocking && ' · 진행 보류'} — {r.description}
        {r.acceptance_criteria.length > 0 && <small>AC: {r.acceptance_criteria.join(' / ')}</small>}
        {state?.gap_links.filter(l => l.requirement_id === r.id).map(l => (
          <small key={l.gap.id}>
            연결된 확인 사항: {l.gap.id} {l.gap.description}
            {editable && !draft && (
              <span className="actions">
                <button type="button" onClick={() => review({ type: 'unlink_gap', gap_id: l.gap.id })}>연결 취소</button>
              </span>
            )}
          </small>
        ))}
        {draft?.id === r.id ? draftForm : editable && (
          <span className="actions">
            {r.status === 'proposed' && <button key="accept" type="button" onClick={() => review({ type: 'accept', ids: [r.id] })}>수락</button>}
            <button key="edit" type="button" disabled={!!draft}
              onClick={() => setDraft({ id: r.id, category: r.category, description: r.description, ac: r.acceptance_criteria.join('\n'), confirm: false })}>수정</button>
          </span>
        )}
      </li>
    )
  }

  function prdSection(title: string, items: (PRDItem & { target_id?: string })[]) {
    return (
      <div key={title}>
        <h3>{title}</h3>
        {items.length === 0 ? <p>없음</p> : (
          <ul>
            {items.map(item => (
              <li key={item.id}>
                <strong>{item.id}</strong>{item.target_id && ` → ${item.target_id}`} — {item.description}
                <small>출처: {[...(item.source_gap_ids ?? []), ...item.source_requirement_ids].join(', ') || '서비스 전반'}</small>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <main>
      <h1>자연어 요구사항 분석</h1>
      <p>서비스 요구사항을 입력하면 구조화된 항목과 확인이 필요한 부분을 표시합니다.</p>
      <form onSubmit={submit}>
        <label htmlFor="requirement-text">서비스 요구사항</label>
        <textarea id="requirement-text" value={text} onChange={event => setText(event.target.value)} rows={5} disabled={!!state} />
        {state
          ? <button key="reset" type="button" onClick={reset} disabled={!!loading}>처음부터 다시</button>
          : <button key="analyze" disabled={!!loading}>분석하기</button>}
      </form>
      {loading && <p className="notice" role="status">{loading}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {state && (
        <section aria-live="polite">
          <p className="notice">{MODE_LABELS[state.analysis_mode]}</p>

          {state.phase === 'clarifying' && state.questions.length > 0 && (
            <form onSubmit={event => { event.preventDefault(); step(state) }}>
              <h2>확인 질문 (라운드 {state.clarification_round}/{MAX_ROUNDS})</h2>
              <p>답하기 어려운 질문은 비워 두면 건너뜁니다. {MAX_ROUNDS}라운드 뒤에도 남은 항목은 AI 제안으로 전환됩니다.</p>
              {state.questions.map(q => (
                <div key={q.gap_id}>
                  <label htmlFor={`answer-${q.gap_id}`}><strong>{q.gap_id}</strong> {q.question}</label>
                  <textarea id={`answer-${q.gap_id}`} rows={2} value={answers[q.gap_id] ?? ''} disabled={!!loading}
                    onChange={event => setAnswers({ ...answers, [q.gap_id]: event.target.value })} />
                </div>
              ))}
              <button disabled={!!loading}>답변 제출</button>
            </form>
          )}
          {state.phase === 'clarifying' && state.questions.length === 0 && !loading && (
            <button type="button" onClick={() => step(state)}>확인 질문 생성 다시 시도</button>
          )}
          {/* review 이후 gaps는 마지막 분석 결과라 blocking 값을 신뢰하지 않는다. phase와 requirements로만 판단 */}
          {state.phase !== 'clarifying' && (
            <>
              <h2>Requirement Review</h2>
              <p className="notice">
                {state.phase === 'review'
                  ? 'Clarification이 끝났습니다. 요구사항을 확인·수정하고 승인하세요.'
                  : '승인했습니다. 아래 JSON이 PRD 생성 단계의 입력입니다.'}
              </p>
              {GROUPS.map(([title, status]) => {
                const items = state.requirements.filter(r => r.status === status)
                return (
                  <div key={status}>
                    <h3>{title} ({items.length})</h3>
                    {status === 'proposed' && editable && items.length > 1 && (
                      <button type="button" onClick={() => review({ type: 'accept', ids: items.map(r => r.id) })}>모두 수락</button>
                    )}
                    {items.length === 0 ? <p>없음</p> : <ul>{items.map(reviewItem)}</ul>}
                  </div>
                )
              })}
              {state.phase === 'review' && (
                <>
                  {draft?.id === '' && !draft.gapId
                    ? <div key="add-form"><h3>요구사항 추가</h3>{draftForm}</div>
                    : <button key="add" type="button" disabled={!editable || !!draft}
                        onClick={() => setDraft({ id: '', category: 'functional', description: '', ac: '', confirm: false })}>요구사항 추가</button>}
                  <h3>남은 확인 사항 ({openGaps.length})</h3>
                  <p>진행을 막지 않지만 아직 정하지 않은 사항입니다. 그대로 승인하면 PRD의 Open Issues로 인계됩니다. 지금 정했다면 결정을 입력하세요. 입력한 결정은 요구사항으로 추가됩니다. 이미 있는 요구사항과 같은 내용이면 추천대로 연결해 하나로 합칠 수 있습니다.</p>
                  {editable && suggested.length > 0 && (
                    <button type="button" disabled={!!draft}
                      onClick={() => review({ type: 'link_gap', gap_ids: suggested.map(gap => gap.id) })}>추천 모두 연결 ({suggested.length})</button>
                  )}
                  {openGaps.length === 0 ? <p>없음</p> : (
                    <ul>
                      {openGaps.map(gap => (
                        <li key={gap.id}>
                          <strong>{gap.id}</strong> [{gap.category}] — {gap.description}
                          <small>관련 요구사항: {gap.related_requirement_ids.join(', ') || '서비스 전반'}</small>
                          {suggestionFor(gap) && (
                            <small>추천: {suggestionFor(gap)!.id} "{suggestionFor(gap)!.description}"와 같은 내용으로 보이면 연결하세요.</small>
                          )}
                          {draft?.gapId === gap.id ? draftForm : editable && (
                            <span className="actions">
                              {suggestionFor(gap) && (
                                <button type="button" disabled={!!draft}
                                  onClick={() => review({ type: 'link_gap', gap_ids: [gap.id] })}>추천대로 연결</button>
                              )}
                              <button type="button" disabled={!!draft}
                                onClick={() => setDraft({ id: '', category: '', description: '', ac: '', confirm: false, gapId: gap.id })}>결정 입력</button>
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  <h3>승인</h3>
                  {pending.length > 0 && (
                    <p className="error">진행 보류 항목({pending.map(r => r.id).join(', ')})을 수정 화면에서 확정해야 승인할 수 있습니다.</p>
                  )}
                  {draft && <p className="error">편집 중인 항목을 저장하거나 취소해야 승인할 수 있습니다.</p>}
                  {proposals.length > 0 && (
                    <p>수락하지 않은 AI 제안 {proposals.length}개는 proposed 상태로 인계됩니다. M1 PRD에서는 Assumptions 후보, M2 PRD에서는 AI 기본값으로 구현 범위에 포함됩니다.</p>
                  )}
                  <button key="approve" type="button" disabled={!editable || pending.length > 0 || !!draft}
                    onClick={() => review({ type: 'approve' })}>요구사항 승인</button>
                </>
              )}
              {reviewed && (
                <>
                  <h3>인계 JSON (ReviewedRequirements)</h3>
                  <pre>{JSON.stringify(reviewed, null, 2)}</pre>
                  <button key="prd" type="button" onClick={generatePrd} disabled={!!loading}>M1 PRD 생성 (규칙 기반 미리보기)</button>
                  <M2Panel key={state.run_id + reviewed.requirements.length} state={state} />
                </>
              )}
              {prdResult && (
                <>
                  <h2>PRD Preview</h2>
                  <p className={prdResult.validation.passed ? 'notice' : 'error'}>
                    검증 {prdResult.validation.passed ? '통과' : '실패'}
                  </p>
                  {prdResult.validation.errors.map(message => <p key={message} className="error">오류: {message}</p>)}
                  {prdResult.validation.warnings.map(message => <p key={message} className="notice">경고: {message}</p>)}
                  <h3>1. Product Overview</h3>
                  <p>{prdResult.prd.overview.summary}</p>
                  {PRD_SECTIONS.map(([title, key]) => prdSection(title, prdResult.prd[key]))}
                  {prdResult.validation.passed ? (
                    <>
                      <h3>Final PRD JSON</h3>
                      <pre>{JSON.stringify(prdResult.prd, null, 2)}</pre>
                    </>
                  ) : (
                    <p className="error">검증을 통과하지 못해 Final PRD로 출력하지 않습니다.</p>
                  )}
                  <button key="back" type="button" onClick={backToReview} disabled={!!loading || !approvedFrom}>Review로 돌아가 수정</button>
                </>
              )}
            </>
          )}

          {state.phase === 'clarifying' && (
            <>
              <h2>현재 요구사항</h2>
              <ul>
                {state.requirements.map(requirement => (
                  <li key={requirement.id}>
                    <strong>{requirement.id}</strong> [{requirement.category} · {requirement.status} · {requirement.source}]
                    {requirement.blocking && ' · 진행 보류'} — {requirement.description}
                  </li>
                ))}
              </ul>
              <h2>확인이 필요한 부분</h2>
              {state.gaps.length === 0 ? <p>발견된 Gap이 없습니다.</p> : (
                <ul>
                  {state.gaps.map(gap => (
                    <li key={gap.id}>
                      <strong>{gap.id}</strong> [{gap.category} · {gap.blocking ? '핵심' : '참고'}]
                      {' '}{gap.description}
                      <small>관련 요구사항: {gap.related_requirement_ids.join(', ') || '서비스 전반'}</small>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}
    </main>
  )
}

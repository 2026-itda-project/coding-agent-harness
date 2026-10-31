import { useState, type FormEvent } from 'react'
import { post } from './api'
import Generation, { type RunStatus } from './Generation'

// 생성 중인 Run을 가리키는 브라우저 메모. 실제 진행 상태와 산출물은 서버에 저장되어 있어 새로고침 뒤 여기서 다시 불러온다.
const RUN_KEY = 'harness.currentRun'

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

type ClarificationQuestion = { gap_id: string; question: string; ai_proposal: string }
type Answer = { gap_id: string; answer: string }
type ClarificationRound = { round: number; gaps: Gap[]; questions: ClarificationQuestion[]; answers: Answer[] }

export type WorkflowState = Analysis & {
  text: string
  phase: 'clarifying' | 'review' | 'approved'
  questions: ClarificationQuestion[]
  history: ClarificationRound[]
  edit_count: number
  gap_links: GapLink[]
  proposal_conversions?: ProposalConversion[]
}

type ReviewAction =
  | { type: 'accept'; ids: string[] }
  | { type: 'edit'; id: string; description: string; confirm: boolean }

const STEPS = ['요구사항 입력', '질문', '검토', '생성', '완료'] as const

function storedRun() {
  try { return localStorage.getItem(RUN_KEY) } catch { return null }
}

function rememberRun(runId: string | null) {
  try {
    if (runId) localStorage.setItem(RUN_KEY, runId)
    else localStorage.removeItem(RUN_KEY)
  } catch { /* 저장소를 못 써도 생성은 서버에서 계속된다 */ }
}

export default function App() {
  const [text, setText] = useState('')
  const [state, setState] = useState<WorkflowState | null>(null)
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [runId, setRunId] = useState<string | null>(storedRun)
  const [runOutcome, setRunOutcome] = useState<RunStatus['outcome'] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState('')
  const [draft, setDraft] = useState<{ id: string; description: string } | null>(null)
  const [editedIds, setEditedIds] = useState<string[]>([])

  async function call<T>(label: string, fn: () => Promise<T>): Promise<T | undefined> {
    setLoading(label)
    setError('')
    try {
      return await fn()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '요청에 실패했습니다.')
    } finally {
      setLoading('')
    }
  }

  // 질문 대기 중이면 답변 라운드, 아니면 분석 직후 첫 호출(answers 없이)
  async function step(current: WorkflowState) {
    const waiting = current.questions.length > 0
    const body = waiting
      ? { state: current, answers: current.questions.map(q => ({ gap_id: q.gap_id, answer: answers[q.gap_id] ?? '' })) }
      : { state: current }
    const next = await call(waiting ? '답변을 반영하고 있습니다…' : '확인할 내용을 찾고 있습니다…',
      () => post<WorkflowState>('/api/clarification/step', body))
    if (next) {
      setState(next)
      setAnswers({})
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!text.trim()) {
      setError('만들고 싶은 서비스를 입력해 주세요.')
      return
    }
    const analysis = await call('요구사항을 분석하고 있습니다…', () => post<Analysis>('/api/requirements/analyze', { text }))
    if (!analysis) return
    const initial: WorkflowState = { ...analysis, text, phase: 'clarifying', questions: [], history: [], edit_count: 0, gap_links: [] }
    setState(initial)
    await step(initial)
  }

  async function review(action: ReviewAction) {
    if (!state) return
    const result = await call('저장하고 있습니다…',
      () => post<{ state: WorkflowState }>('/api/review', { state, action }))
    if (result) {
      setState(result.state)
      if (action.type === 'edit') setEditedIds(ids => ids.includes(action.id) ? ids : [...ids, action.id])
      setDraft(null)
    }
  }

  // 생성 버튼 하나: 서버가 Review 승인, PRD 생성과 검증, 개발 명세부터 점검까지를 이어서 진행한다.
  async function generate() {
    if (!state) return
    const result = await call('생성을 시작하고 있습니다…', () => post<{ run_id: string }>('/api/m2/generate', { state }))
    if (result) {
      setRunOutcome(null)
      rememberRun(result.run_id)
      setRunId(result.run_id)
    }
  }

  // 화면만 처음으로 돌린다. 서버에 저장된 입력, PRD, 실행 기록과 생성 결과는 지우지 않는다.
  function home() {
    if (homeDisabled) return
    rememberRun(null)
    setRunId(null)
    setRunOutcome(null)
    setState(null)
    setText('')
    setAnswers({})
    setDraft(null)
    setEditedIds([])
    setError('')
  }

  const screen = runId ? 'run' : !state ? 'input' : state.phase === 'clarifying' ? 'questions' : 'review'
  const finished = runOutcome === 'auto_checks_passed' || runOutcome === 'manual_check_only'
  const stepIndex = screen === 'run' && finished ? 4 : { input: 0, questions: 1, review: 2, run: 3 }[screen]
  const editable = !loading && !draft
  const homeDisabled = !!loading || !!runId && (runOutcome === null || runOutcome === 'running')

  // 최초 입력의 해석은 필요한 경우에만 펼친다. 검토에서 수정한 항목은 '정한 내용'에 계속 보인다.
  const reqs = state?.requirements ?? []
  const decisions = reqs.filter(r => r.status === 'confirmed' && r.source !== 'ai_proposal'
    && (r.source === 'clarification_answer' || r.source === 'review_input' || editedIds.includes(r.id)))
  const inputItems = reqs.filter(r => r.status === 'confirmed' && r.source === 'initial_input' && !editedIds.includes(r.id))
  const aiItems = reqs.filter(r => r.source === 'ai_proposal' && r.status !== 'needs_clarification')
  const undecided = reqs.filter(r => r.status === 'needs_clarification')
  const proposals = aiItems.filter(r => r.status === 'proposed')
  const required = undecided.filter(r => r.blocking)

  function editor(r: Requirement, confirmLabel: string) {
    if (draft?.id !== r.id) return null
    return (
      <form className="editor" onSubmit={event => {
        event.preventDefault()
        if (!draft.description.trim()) { setError('내용을 입력해 주세요.'); return }
        review({ type: 'edit', id: r.id, description: draft.description.trim(), confirm: true })
      }}>
        <textarea aria-label="요구사항 수정" rows={2} value={draft.description} autoFocus
          onChange={event => setDraft({ ...draft, description: event.target.value })} />
        <div className="row">
          <button disabled={!!loading}>{confirmLabel}</button>
          <button type="button" className="secondary" onClick={() => setDraft(null)} disabled={!!loading}>취소</button>
        </div>
      </form>
    )
  }

  return (
    <div className="page">
      <header className="top">
        <button type="button" className="brand" onClick={home} disabled={homeDisabled}>Harness</button>
        <ol className="stepper" aria-label="진행 단계">
          {STEPS.map((label, index) => (
            <li key={label} className={index < stepIndex ? 'done' : index === stepIndex ? 'current' : ''}
              aria-current={index === stepIndex ? 'step' : undefined}>{label}</li>
          ))}
        </ol>
      </header>

      <main>
        {loading && <p className="notice" role="status"><span className="spinner" aria-hidden /> {loading}</p>}
        {error && <p role="alert" className="error">{error}</p>}

        {screen === 'run' && runId && <Generation key={runId} runId={runId} onHome={home} onOutcome={setRunOutcome} />}

        {screen === 'input' && (
          <section className="card hero">
            <h1>어떤 서비스를 만들까요?</h1>
            <p className="muted">만들고 싶은 서비스를 자유롭게 설명해 주세요. 꼭 필요한 것만 몇 가지 물어본 뒤 동작하는 앱을 만들어 드립니다.</p>
            <form onSubmit={submit}>
              <label htmlFor="requirement-text" className="sr-only">서비스 설명</label>
              <textarea id="requirement-text" value={text} onChange={event => setText(event.target.value)} rows={6}
                placeholder="예: 동네 소모임을 만들고 참여 신청을 받을 수 있는 서비스를 만들어줘" disabled={!!loading} />
              <button disabled={!!loading}>시작하기</button>
            </form>
          </section>
        )}

        {screen === 'questions' && state && (
          state.questions.length > 0 ? (
            <section className="card">
              <h2>몇 가지만 확인할게요</h2>
              <p className="muted">답하기 어려운 질문은 비워 두셔도 됩니다. 다음에 다시 여쭤보고, 끝까지 비어 있으면 AI가 정합니다.</p>
              <form onSubmit={event => { event.preventDefault(); step(state) }}>
                {state.questions.map((q, index) => (
                  <div key={q.gap_id} className="question">
                    <label htmlFor={`answer-${q.gap_id}`}>{index + 1}. {q.question}</label>
                    <textarea id={`answer-${q.gap_id}`} rows={2} value={answers[q.gap_id] ?? ''} disabled={!!loading}
                      onChange={event => setAnswers({ ...answers, [q.gap_id]: event.target.value })} />
                  </div>
                ))}
                <button disabled={!!loading}>답변 제출</button>
              </form>
            </section>
          ) : !loading && (
            <section className="card">
              <p>확인할 내용을 불러오지 못했습니다.</p>
              <button type="button" onClick={() => step(state)}>다시 시도</button>
            </section>
          )
        )}

        {screen === 'review' && state && (
          <section className="card">
            <h2>이렇게 만들까요?</h2>
            <p className="muted">질문에 답한 내용과 AI가 정한 내용입니다. 고칠 부분만 수정하고 생성을 누르세요.</p>
            {state.analysis_mode !== 'claude' && <p className="notice small">개발용 {state.analysis_mode} 분석 결과입니다.</p>}
            {state.history.every(round => round.questions.length === 0) && (
              <p className="notice small">추가로 확인할 질문이 없어 바로 검토로 넘어왔습니다.</p>
            )}

            {inputItems.length > 0 && (
              <details className="input-review">
                <summary>입력 내용이 어떻게 정리됐는지 보기</summary>
                <p className="muted small">처음 입력하신 내용을 정리했습니다. 다르게 이해한 부분만 수정해 주세요. 모두 확인하지 않아도 생성할 수 있습니다.</p>
                <ul className="items">{inputItems.map(r => (
                  <li key={r.id}>
                    {r.description}
                    {editor(r, '저장') ?? (
                      <button type="button" className="link" disabled={!editable}
                        onClick={() => setDraft({ id: r.id, description: r.description })}>수정</button>
                    )}
                  </li>
                ))}</ul>
              </details>
            )}

            {decisions.length > 0 && (
              <>
                <h3>정한 내용</h3>
                <ul className="items">{decisions.map(r => (
                  <li key={r.id}>
                    {r.description}
                    {editor(r, '저장') ?? (
                      <button type="button" className="link" disabled={!editable}
                        onClick={() => setDraft({ id: r.id, description: r.description })}>수정</button>
                    )}
                  </li>
                ))}</ul>
              </>
            )}

            {undecided.length > 0 && (
              <>
                <h3>정해야 할 내용</h3>
                <ul className="items">
                  {undecided.map(r => (
                    <li key={r.id}>
                      {r.description}{r.blocking && <span className="badge warn">필수</span>}
                      {editor(r, '정하기') ?? (
                        <button type="button" className="link" disabled={!editable}
                          onClick={() => setDraft({ id: r.id, description: '' })}>정하기</button>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}

            {aiItems.length > 0 && (
              <>
                <div className="row between">
                  <h3>AI가 정한 내용</h3>
                  {proposals.length > 1 && (
                    <button type="button" className="secondary" disabled={!editable}
                      onClick={() => review({ type: 'accept', ids: proposals.map(r => r.id) })}>모두 좋아요</button>
                  )}
                </div>
                <p className="muted small">따로 손대지 않아도 이대로 만들어집니다.</p>
                <ul className="items">
                  {aiItems.map(r => (
                    <li key={r.id}>
                      {r.description}<span className="badge ai">{r.status === 'proposed' ? 'AI 제안' : '확인한 AI 제안'}</span>
                      {editor(r, '저장') ?? (
                        <span className="row inline">
                          {r.status === 'proposed' && (
                            <button type="button" className="link" disabled={!editable}
                              onClick={() => review({ type: 'accept', ids: [r.id] })}>좋아요</button>
                          )}
                          <button type="button" className="link" disabled={!editable}
                            onClick={() => setDraft({ id: r.id, description: r.description })}>수정</button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}

            {required.length > 0 && <p className="error">필수 항목을 정해야 생성할 수 있습니다.</p>}
            <div className="row">
              <button type="button" className="primary" onClick={generate} disabled={!editable || required.length > 0}>생성</button>
              <button type="button" className="secondary" onClick={home} disabled={!!loading}>처음부터 다시</button>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { get, post } from './api'
import type { WorkflowState } from './App'

type Item = {
  id: string
  description: string
  status: 'confirmed' | 'proposed' | 'needs_clarification'
  source: string
  requirement_ids: string[]
  gap_ids: string[]
}

type M2PRD = {
  title: string
  overview: { summary: string; problem: string; goals: string[] }
  in_scope: Item[]
  out_of_scope: Item[]
  roles: Item[]
  user_flows: Item[]
  functional_requirements: Item[]
  business_rules: Item[]
  nfrs: Item[]
  acceptance_criteria: { id: string; description: string; target_ids: string[] }[]
  open_issues: Item[]
}

type PrdResult = {
  prd_id: string
  status: 'passed' | 'rejected'
  errors: string[]
  prd?: M2PRD
  run_id?: string | null
}

type RunStatus = {
  run_id: string
  outcome: 'running' | 'failed' | 'interrupted' | 'verification_failed' | 'auto_checks_passed' | 'manual_check_only'
  error: string | null
  current_stage: string | null
  tasks: { id: string; title: string; status: string; attempt: number }[]
  task_order: string[]
  verify: { status: string; automatic_status: string | null; passed: number; total: number; failures: string[] } | null
  manual_review: 'pending' | 'done'
  preview_url: string | null
  cost_usd: { messages_api: number; agent: number; unknown_cost_records: number } | null
}

const SECTIONS = [
  ['범위 (포함)', 'in_scope'], ['범위 (제외)', 'out_of_scope'], ['사용자 / 역할', 'roles'],
  ['사용 흐름', 'user_flows'], ['기능 요구사항', 'functional_requirements'], ['업무 규칙', 'business_rules'],
  ['비기능 요구사항', 'nfrs'], ['미결정 사항 (Open Issues)', 'open_issues'],
] as const
const STATUS_LABELS = { confirmed: '확정', proposed: 'AI 기본값 (미수락)', needs_clarification: '미결정' } as const
const SOURCE_LABELS: Record<string, string> = {
  initial_input: '최초 입력', clarification_answer: '질문 답변', review_input: 'Review 추가',
  ai_proposal: 'AI 제안', harness_policy: '생성 범위 정책',
}
const STAGE_LABELS: Record<string, string> = { spec: '개발 명세(Spec) 생성', plan: '작업 계획 생성', code: '코드 작성', verify: '빌드·자동 점검' }
const OUTCOME_LABELS: Record<RunStatus['outcome'], string> = {
  running: '생성 중',
  failed: '실패',
  interrupted: '중단됨 (서버 재시작)',
  verification_failed: '자동 점검 실패',
  auto_checks_passed: '자동 점검 통과 · 사람 확인 대기',
  manual_check_only: '자동 점검 없음, 사람 확인 대기',
}

export default function M2Panel({ state }: { state: WorkflowState }) {
  const [prd, setPrd] = useState<PrdResult | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [pollKey, setPollKey] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label)
    setError('')
    try {
      await fn()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '요청에 실패했습니다.')
    } finally {
      setBusy('')
    }
  }

  const generatePrd = () => act('Review 결과로 PRD 생성 중… (Sonnet, 1~2분)', async () => {
    const result = await post<PrdResult>('/api/m2/prd', { state })
    setPrd(result)
    if (result.run_id) setRunId(result.run_id)
  })

  const startRun = () => act('MVP 생성 요청 중…', async () => {
    if (!prd) return
    const result = await post<{ run_id: string }>('/api/m2/runs', { prd_id: prd.prd_id, confirm_open_issues: confirmed })
    setRunId(result.run_id)
  })

  // 실패·중단된 같은 Run을 이어서 실행한다. 이전 시도의 기록과 비용은 서버가 그대로 합산한다.
  const retryRun = () => act('이어서 다시 시도하는 중…', async () => {
    if (!prd || !runId) return
    await post('/api/m2/runs', { prd_id: prd.prd_id, confirm_open_issues: confirmed, retry: true })
    setPollKey(key => key + 1)
  })

  const stopPreview = () => act('미리보기 종료 중…', async () => {
    if (!runId) return
    await post(`/api/m2/runs/${runId}/preview/stop`, {})
    setStatus(await get<RunStatus>(`/api/m2/runs/${runId}`))
  })

  // 실행 중에는 상태를 주기적으로 조회한다. 실행 자체는 서버가 백그라운드로 진행한다.
  useEffect(() => {
    if (!runId) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await get<RunStatus>(`/api/m2/runs/${runId}`)
        if (stopped) return
        setStatus(next)
        if (next.outcome === 'running') timer = setTimeout(poll, 4000)
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : '상태 조회에 실패했습니다.')
      }
    }
    poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [runId, pollKey])

  const openIssues = prd?.prd?.open_issues ?? []
  const canRun = prd?.status === 'passed' && !runId && !busy && (openIssues.length === 0 || confirmed)

  return (
    <section aria-labelledby="m2-title">
      <h2 id="m2-title">MVP 생성 (M2)</h2>
      <p>승인한 요구사항으로 PRD를 만들고, 확인한 뒤 실제 동작하는 데모 앱 생성을 요청합니다. 각 단계는 Claude API 비용이 발생합니다.</p>
      {!prd && <button type="button" onClick={generatePrd} disabled={!!busy}>M2 PRD 생성 (Sonnet)</button>}
      {busy && <p className="notice" role="status">{busy}</p>}
      {error && <p role="alert" className="error">{error}</p>}

      {prd?.status === 'rejected' && (
        <>
          <p className="error">생성된 PRD가 검증을 통과하지 못해 MVP 생성을 시작할 수 없습니다.</p>
          <ul>{prd.errors.map(message => <li key={message} className="error">{message}</li>)}</ul>
          <button type="button" onClick={generatePrd} disabled={!!busy}>PRD 다시 생성</button>
        </>
      )}

      {prd?.prd && (
        <>
          <h3>{prd.prd.title}</h3>
          <p>{prd.prd.overview.summary}</p>
          {SECTIONS.map(([title, key]) => (
            <div key={key}>
              <h4>{title}</h4>
              {prd.prd![key].length === 0 ? <p>없음</p> : (
                <ul>
                  {prd.prd![key].map(item => (
                    <li key={item.id}>
                      <strong>{item.id}</strong>{' '}
                      <span className={item.source === 'ai_proposal' ? 'badge ai' : 'badge'}>
                        {STATUS_LABELS[item.status]} · {SOURCE_LABELS[item.source] ?? item.source}
                      </span> — {item.description}
                      {(item.requirement_ids.length > 0 || item.gap_ids.length > 0) && (
                        <small>근거: {[...item.requirement_ids, ...item.gap_ids].join(', ')}</small>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <h4>완료 조건 (Acceptance Criteria)</h4>
          <ul>{prd.prd.acceptance_criteria.map(ac => <li key={ac.id}><strong>{ac.id}</strong> → {ac.target_ids.join(', ')} — {ac.description}</li>)}</ul>

          {!runId && (
            <>
              {openIssues.length > 0 && (
                <label>
                  <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{' '}
                  미결정 사항 {openIssues.length}건이 남은 채로 생성하는 것에 동의합니다.
                </label>
              )}
              <button type="button" onClick={startRun} disabled={!canRun}>이 PRD로 MVP 생성</button>
            </>
          )}
        </>
      )}

      {status && (
        <div role="status" aria-live="polite">
          <h3>실행 상태: {OUTCOME_LABELS[status.outcome]}</h3>
          <p><small>실행 ID {status.run_id}</small></p>
          {status.outcome === 'running' && status.current_stage && <p className="notice">현재 단계: {STAGE_LABELS[status.current_stage] ?? status.current_stage}</p>}
          {status.error && <p className="error">실패 사유: {status.error}</p>}
          {status.tasks.length > 0 && (
            <ul>{status.tasks.map(task => <li key={task.id}>{task.id} {task.title} — {task.status === 'done' ? '완료' : '실패'} (시도 {task.attempt})</li>)}</ul>
          )}
          {status.verify && (status.verify.automatic_status === 'not_applicable' ? <p>실행한 자동 점검이 없습니다.</p> : (
            <p className={status.verify.failures?.length ? 'error' : 'notice'}>
              자동 점검 {status.verify.passed}/{status.verify.total} 통과
              {status.verify.failures?.length > 0 && ` — ${status.verify.failures.join(', ')}`}
            </p>
          ))}
          {(status.outcome === 'auto_checks_passed' || status.outcome === 'manual_check_only') && status.manual_review === 'pending' && (
            <p>사람 확인이 남아 있습니다. 미리보기에서 주요 흐름을 직접 확인해야 완료로 볼 수 있습니다.</p>
          )}
          {(status.outcome === 'failed' || status.outcome === 'interrupted') && (
            <>
              <p>실행 기록은 작업 폴더의 artifacts에 남아 있습니다. 자동 재시도는 하지 않습니다. 다시 시도하면 완료된 단계는 건너뛰고 이어서 실행하며 추가 비용이 발생합니다.</p>
              <p>다시 시도하여 검증 단계가 실행되면 미리보기에서 입력한 데이터는 초기화되고, 직전 데이터 한 벌만 보관됩니다.</p>
              {(status.cost_usd?.unknown_cost_records ?? 0) > 0 ? (
                <>
                  <p className="error">비용 확인이 필요합니다. 운영자가 중단된 호출의 비용을 확인한 뒤 다시 시도할 수 있습니다.</p>
                  <button type="button" disabled={!!busy} onClick={() => setPollKey(key => key + 1)}>비용 확인 상태 새로고침</button>
                </>
              ) : <button type="button" onClick={retryRun} disabled={!!busy}>이어서 다시 시도</button>}
            </>
          )}
          {status.cost_usd && (
            <p><small>이번 실행 비용(계산값): 생성 단계 ${status.cost_usd.messages_api.toFixed(3)} · 코딩 ${status.cost_usd.agent.toFixed(3)}
              {status.cost_usd.unknown_cost_records > 0 && ` · 비용 미확인 기록 ${status.cost_usd.unknown_cost_records}건`}</small></p>
          )}
          {status.preview_url && (
            <>
              <h3>생성 앱 미리보기</h3>
              <p><a href={status.preview_url} target="_blank" rel="noreferrer">새 창에서 열기 ({status.preview_url})</a></p>
              <iframe title="생성 앱 미리보기" src={status.preview_url} className="preview" />
              <button type="button" onClick={stopPreview} disabled={!!busy}>미리보기 종료</button>
            </>
          )}
        </div>
      )}
    </section>
  )
}

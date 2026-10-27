import { useEffect, useState } from 'react'
import { get, post } from './api'

// 서버 GET /api/m2/runs/{run_id} 응답 중 화면에 쓰는 부분. 비용, 토큰, PRD 같은 내부 기록은 서버에만 남는다.
export type RunStatus = {
  run_id: string
  prd_id: string
  outcome: 'running' | 'awaiting_confirmation' | 'failed' | 'interrupted' | 'verification_failed'
    | 'auto_checks_passed' | 'manual_check_only'
  error: string | null
  current_stage: 'prd' | 'spec' | 'plan' | 'code' | 'build' | 'check' | null
  tasks: { id: string; status: string }[]
  task_order: string[]
  preview_url: string | null
  open_issues: string[]
}

const POLL_MS = 10_000
const STEPS = [
  ['prd', '요구사항 정리'], ['spec', '개발 명세 생성'], ['plan', '작업 계획 생성'],
  ['code', '기능 구현'], ['build', '빌드'], ['check', '최종 점검'],
] as const

// 진행 표시는 서버의 실행 기록(현재 단계, 완료된 Task 수)만 쓴다. 예상 시간이나 가짜 진행률은 만들지 않는다.
function stepLabel(status: RunStatus, key: string, label: string) {
  if (key !== 'code' || status.task_order.length === 0) return label
  const done = status.tasks.filter(t => t.status === 'done').length
  return `${label} ${done}/${status.task_order.length}개 완료`
}

export default function Generation({ runId, onHome }: { runId: string; onHome: () => void }) {
  const [status, setStatus] = useState<RunStatus | null>(null)
  const [pollKey, setPollKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await get<RunStatus>(`/api/m2/runs/${runId}`)
        if (stopped) return
        setStatus(next)
        setError('')
        if (next.outcome === 'running') timer = setTimeout(poll, POLL_MS)
      } catch (cause) {
        if (stopped) return
        setError(cause instanceof Error ? cause.message : '진행 상태를 불러오지 못했습니다.')
        timer = setTimeout(poll, POLL_MS)
      }
    }
    poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [runId, pollKey])

  // 같은 Run을 이어서 진행한다(미결정 사항 확인 또는 실패 후 다시 시도). 서버가 중복 실행을 막는다.
  async function resume(body: { confirm_open_issues?: boolean; retry?: boolean }) {
    if (!status) return
    setBusy(true)
    setError('')
    try {
      await post('/api/m2/runs', { prd_id: status.prd_id, ...body })
      setPollKey(key => key + 1)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '요청에 실패했습니다.')
    } finally {
      setBusy(false)
    }
  }

  const home = <button type="button" className="secondary" onClick={onHome}>홈으로</button>
  if (!status) {
    return <section className="card">{error ? <p role="alert" className="error">{error}</p> : <p>불러오는 중…</p>}{error && home}</section>
  }

  if (status.outcome === 'running') {
    const current = STEPS.findIndex(([key]) => key === status.current_stage)
    return (
      <section className="card" aria-live="polite">
        <h2>서비스를 만들고 있습니다</h2>
        <p className="muted">단계마다 몇 분씩 걸릴 수 있습니다. 이 창을 닫거나 새로고침해도 생성은 계속되고, 다시 열면 이어서 볼 수 있습니다.</p>
        <ol className="progress">
          {STEPS.map(([key, label], index) => (
            <li key={key} className={index < current ? 'done' : index === current ? 'active' : ''}>
              {index === current && <span className="spinner" aria-hidden />}
              {stepLabel(status, key, label)}{index === current && ' 중'}
            </li>
          ))}
        </ol>
        {error && <p role="alert" className="error">{error}</p>}
      </section>
    )
  }

  if (status.outcome === 'awaiting_confirmation') {
    return (
      <section className="card">
        <h2>아직 정하지 않은 내용이 있습니다</h2>
        <p>아래 내용은 정해지지 않은 채로 만들어집니다. 이대로 계속할까요?</p>
        <ul>{status.open_issues.map(issue => <li key={issue}>{issue}</li>)}</ul>
        {error && <p role="alert" className="error">{error}</p>}
        <div className="row">
          <button type="button" onClick={() => resume({ confirm_open_issues: true })} disabled={busy}>이대로 계속 생성</button>
          {home}
        </div>
      </section>
    )
  }

  if (status.outcome === 'auto_checks_passed' || status.outcome === 'manual_check_only') {
    return (
      <section className="card">
        <h2>{status.outcome === 'auto_checks_passed' ? '생성이 완료되었습니다!' : '생성이 끝났습니다'}</h2>
        {status.outcome === 'manual_check_only' && (
          <p className="muted">자동으로 확인할 항목이 없었습니다. 미리보기에서 직접 확인해 주세요.</p>
        )}
        {status.preview_url ? (
          <>
            <iframe title="생성 앱 미리보기" src={status.preview_url} className="preview" />
            <div className="row">
              <a className="button" href={status.preview_url} target="_blank" rel="noreferrer">새 창에서 열기</a>
              {home}
            </div>
          </>
        ) : (
          <>
            <p className="muted">미리보기가 꺼져 있습니다. 생성한 결과는 서버에 저장되어 있습니다.</p>
            {home}
          </>
        )}
      </section>
    )
  }

  const checkFailed = status.outcome === 'verification_failed'
  return (
    <section className="card">
      <h2>서비스를 만들지 못했습니다</h2>
      <p>
        {status.outcome === 'interrupted' && '서버가 다시 시작되어 생성이 멈췄습니다.'}
        {status.outcome === 'verification_failed' && '만든 앱이 빌드 또는 최종 점검을 통과하지 못했습니다.'}
        {status.outcome === 'failed' && '생성 중 문제가 생겨 멈췄습니다.'}
      </p>
      {status.error && <p className="muted small">사유: {status.error}</p>}
      <p className="muted">
        {checkFailed
          ? '다시 시도하면 만든 코드 그대로 빌드와 점검만 다시 합니다. 패키지 설치 같은 일시적인 문제라면 통과할 수 있지만, 코드 문제라면 같은 이유로 다시 실패할 수 있습니다.'
          : '다시 시도하면 끝난 단계는 건너뛰고 멈춘 곳부터 이어서 진행합니다. 추가 비용이 발생할 수 있습니다.'}
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="row">
        <button type="button" onClick={() => resume({ retry: true })} disabled={busy}>{checkFailed ? '빌드와 점검 다시 하기' : '이어서 다시 시도'}</button>
        {home}
      </div>
    </section>
  )
}

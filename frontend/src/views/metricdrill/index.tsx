import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { fetchJson, postAction, request } from '@/api/client'

const ENDPOINT = '/api/metricdrill'
const columns = [
  "分析编号",
  "目标指标",
  "波动方向",
  "波动幅度",
  "维度下钻路径",
  "贡献度排名",
  "根因假设",
  "验证结果"
]
const actions = [
  "发起分析",
  "确认结论",
  "归档分析",
  "核对验证结果",
  "标记来源失效"
]
const statuses = [
  "草稿",
  "分析中",
  "已确认",
  "已归档"
]

type Cell = string | number | boolean | null | undefined | Record<string, unknown> | unknown[]
type Row = Record<string, Cell>

function text(value: Cell, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function rangeText(value: Cell): string {
  return Array.isArray(value) ? value.map((item) => text(item as Cell)).join('~') : text(value)
}

function ruleSummary(rule: Record<string, unknown> | undefined): string {
  if (!rule) return '归因建模规范口径未加载'
  return `归因建模规范口径（核验码 ${text(rule['核验码'] as Cell)}）：`
    + `单触点 ${rangeText(rule['单触点允许范围'] as Cell)}，`
    + `合计 ${rangeText(rule['权重合计阈值'] as Cell)}，`
    + `触点数上限 ${text(rule['触点数上限'] as Cell)}`
}

export default function MetricdrillView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [pendingIds, setPendingIds] = useState<Record<string, boolean>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const stats = useMemo(() => {
    const confirmed = rows.filter((row) => row.status === '已确认').length
    const analyzing = rows.filter((row) => row.status === '分析中').length
    return [
      { label: '归因分析总数', value: total },
      { label: '已确认分析', value: confirmed },
      { label: '分析中分析', value: analyzing },
    ]
  }, [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('归因分析列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    const rowKey = String(row.id)
    setErrorMessage('')
    setNotice('')
    setPendingIds((prev) => ({ ...prev, [rowKey]: true }))
    try {
      const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { action })
      setNotice(result.message)
      if (!result.ok) {
        setErrorMessage(result.message)
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因操作失败')
    } finally {
      setPendingIds((prev) => ({ ...prev, [rowKey]: false }))
    }
  }

  return (
    <section className="page" data-module="metricdrill">
      <header className="page-head">
        <div>
          <h2>指标归因管理</h2>
          <p className="page-desc">对指标异常波动进行多维度归因分析，下钻定位波动根因；模型下线后当前根因立即让位。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('归因分析登记入口尚未接入审批流')}>
            登记归因分析
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出指标归因清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <label className="filter-item">
          <span>状态</span>
          <select
            value={filters.status ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}
          >
            <option value="">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>根因状态</th>
            <th>数据集</th>
            <th>来源失效时间</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const rowKey = String(row.id)
            const busy = Boolean(pendingIds[rowKey])
            const stuck = row['根因状态'] === '来源已失效'
            return (
              <tr key={rowKey} data-root-state={String(row['根因状态'] ?? '')}>
                {columns.map((column) => (
                  <td key={column} className={column === '根因假设' && stuck ? 'cell-muted' : undefined}>
                    {column === '贡献度排名' && row[column]
                      ? <Link to={`/metricdrill/${rowKey}`}>{String(row[column])}</Link>
                      : text(row[column])}
                  </td>
                ))}
                <td>
                  {stuck ? <span className="tag warn">来源已失效</span> : text(row['根因状态'], '未分析')}
                </td>
                <td>{text(row['数据集编号'])}{row['数据集编号'] ? ` · ${text(row['数据集状态'], '待处理')}` : ''}</td>
                <td>{text(row['来源失效时间'])}</td>
                <td className="row-actions">
                  <Link className="link" to={`/metricdrill/${rowKey}`}>下钻明细</Link>
                  {actions.map((action) => (
                    <button
                      key={action}
                      className="link"
                      type="button"
                      disabled={busy}
                      title={busy ? '正在处理，请稍候' : action}
                      onClick={() => void runAction(action, row)}
                    >
                      {busy ? `${action}…` : action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 4}>暂无指标归因数据，可先登记归因分析</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条指标归因记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}

type DetailPayload = {
  分析: Row
  数据集: Row | null
  待选模型: Row[]
  异常批次: Row | null
  落库凭证: Row[]
  当前模型状态: string | null
  当前根因可用: boolean
  口径: Record<string, unknown>
}

export function MetricdrillDetailView() {
  const params = useParams()
  const navigate = useNavigate()
  const entryId = params.id ?? ''
  const [detail, setDetail] = useState<DetailPayload | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    setErrorMessage('')
    if (!entryId) return
    try {
      const payload = await fetchJson<DetailPayload>(`${ENDPOINT}/${entryId}/detail`)
      setDetail(payload)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '下钻明细读取失败')
    }
  }, [entryId])

  useEffect(() => {
    void reload()
  }, [reload])

  async function act(action: string, body: Record<string, unknown> = {}) {
    setBusy(true)
    setErrorMessage('')
    setNotice('')
    try {
      const result = await postAction(`${ENDPOINT}/${entryId}/actions`, { action, ...body })
      setNotice(result.message)
      if (!result.ok) setErrorMessage(result.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '下钻操作失败')
    } finally {
      setBusy(false)
    }
  }

  async function settle(datasetNo: string) {
    setBusy(true)
    setErrorMessage('')
    try {
      const result = await postAction(`${ENDPOINT}/${entryId}/datasets/settle`, { 数据集编号: datasetNo })
      setNotice(result.message)
      if (!result.ok) setErrorMessage(result.message)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据集落定失败')
    } finally {
      setBusy(false)
    }
  }

  if (!detail) {
    return (
      <section className="page" data-module="metricdrill-detail">
        <header className="page-head">
          <h2>指标下钻明细</h2>
          <Link className="link" to="/metricdrill">返回指标归因</Link>
        </header>
        {errorMessage
          ? <p className="error-text">{errorMessage}</p>
          : <p className="page-desc">正在加载下钻明细…</p>}
      </section>
    )
  }

  const analysis = detail.分析
  const dataset = detail.数据集
  const detailRows = (dataset?.['贡献度明细'] as Row[] | undefined) ?? []
  const invalid = analysis['根因状态'] === '来源已失效'

  return (
    <section className="page" data-module="metricdrill-detail">
      <header className="page-head">
        <div>
          <h2>指标下钻明细 · {text(analysis['分析编号'])}</h2>
          <p className="page-desc">
            {text(analysis['目标指标'])} {text(analysis['波动方向'])} {text(analysis['波动幅度'])}，
            下钻路径 {text(analysis['维度下钻路径'])}
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/metricdrill">返回指标归因</Link>
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">当前根因状态</span>
          <strong className="stat-value">{invalid ? '来源已失效' : text(analysis['根因状态'], '未分析')}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">支撑模型</span>
          <strong className="stat-value">
            {text(analysis['归因模型编号'])} · {text(detail.当前模型状态, '未绑定')}
          </strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">当前根因可用</span>
          <strong className="stat-value">{detail.当前根因可用 ? '可用' : '不可用'}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">有效数据集</span>
          <strong className="stat-value">{dataset ? text(dataset['数据集编号']) : '无'}</strong>
        </article>
      </div>

      {invalid && (
        <div className="banner warn">
          <strong>来源模型已失效：</strong>{text(analysis['来源失效说明'])}
          <span className="banner-side">来源失效时间 {text(analysis['来源失效时间'])}</span>
        </div>
      )}

      <div className="split">
        <div className="panel">
          <h3 className="section-title">当前根因</h3>
          <dl className="kv">
            <div className="kv-row"><dt>根因假设</dt><dd>{text(analysis['根因假设'], '（已让位，见失效前根因）')}</dd></div>
            <div className="kv-row"><dt>贡献度排名</dt><dd>{text(analysis['贡献度排名'])}</dd></div>
            <div className="kv-row"><dt>验证结果</dt><dd>{text(analysis['验证结果'])}</dd></div>
            <div className="kv-row"><dt>原告警时点直接贡献率</dt><dd>{text(analysis['原直接贡献率'])}</dd></div>
            <div className="kv-row"><dt>原告警时点间接贡献率</dt><dd>{text(analysis['原间接贡献率'])}</dd></div>
            <div className="kv-row"><dt>已选处理终点</dt><dd>{text(analysis['已选处理终点'], '尚无处理终点')}</dd></div>
          </dl>
        </div>
        <div className="panel">
          <h3 className="section-title">待选模型</h3>
          {detail.待选模型.length === 0 && <p className="page-desc">暂无待选模型</p>}
          <ul className="plain-list">
            {detail.待选模型.map((model) => (
              <li key={String(model.id)}>
                <span>{text(model['模型编号'])} · {text(model['模型名称'])}</span>
                <span className="muted">{text(model['状态'])}</span>
                <button
                  className="link"
                  type="button"
                  disabled={busy}
                  onClick={() => void act('重新分析', { 归因模型编号: model['模型编号'] })}
                >
                  用该模型重新分析
                </button>
              </li>
            ))}
          </ul>
          {analysis['失效前根因'] && (
            <>
              <h3 className="section-title">失效前根因（原告警时点）</h3>
              <dl className="kv">
                {Object.entries(analysis['失效前根因'] as Record<string, Cell>).map(([key, value]) => (
                  <div key={key} className="kv-row">
                    <dt>{key}</dt><dd>{text(value)}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>
      </div>

      <h3 className="section-title">指标波动归因明细数据集</h3>
      {!dataset && <p className="page-desc">当前没有有效数据集；保存后等待处理，落定后才能确认结论。</p>}
      {dataset && (
        <>
          <dl className="kv inline">
            <div className="kv-row"><dt>数据编号</dt><dd>{text(dataset['数据编号'])}</dd></div>
            <div className="kv-row"><dt>数据集状态</dt><dd>{text(dataset['状态'])}</dd></div>
            <div className="kv-row"><dt>异常批次</dt><dd>{text(dataset['异常批次编号'])}</dd></div>
            <div className="kv-row"><dt>口径判定</dt><dd>{text((dataset['口径校验'] as Row | undefined)?.['判定'])}</dd></div>
            <div className="kv-row"><dt>口径核验码</dt><dd>{text(dataset['口径核验码'])}</dd></div>
            <div className="kv-row">
              <dt>落库凭证</dt>
              <dd>{text((dataset['落库凭证'] as Row | undefined)?.['凭证编号'])}</dd>
            </div>
          </dl>
          <table className="data-table">
            <thead>
              <tr><th>排名</th><th>触点</th><th>触点权重</th><th>归一化权重</th><th>波动贡献基数</th><th>波动贡献度</th><th>贡献度占比</th></tr>
            </thead>
            <tbody>
              {detailRows.map((item) => (
                <tr key={String(item['触点'])}>
                  <td>{text(item['排名'])}</td>
                  <td>{text(item['触点'])}</td>
                  <td>{text(item['触点权重'])}</td>
                  <td>{text(item['归一化权重'])}</td>
                  <td>{text(item['波动贡献基数'])}</td>
                  <td>{text(item['波动贡献度'])}</td>
                  <td>{text(item['贡献度占比'])}%</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="page-actions detail-actions">
            {dataset['状态'] === '待处理' && (
              <button className="btn primary" type="button" disabled={busy} onClick={() => void settle(String(dataset['数据集编号']))}>
                处理数据集
              </button>
            )}
            <button className="btn" type="button" disabled={busy} onClick={() => void act('核对验证结果')}>核对验证结果</button>
            <button className="btn" type="button" disabled={busy} onClick={() => void act('确认结论')}>确认结论</button>
            <button className="btn" type="button" disabled={busy} onClick={() => void act('归档分析')}>归档分析</button>
          </div>
        </>
      )}

      {!dataset && detail.待选模型.length > 0 && (
        <div className="panel">
          <h3 className="section-title">由指标下钻保存归因明细数据集</h3>
          <p className="page-desc">
            选择仍「已发布」的模型，从该模型在本分析异常批次里的触点权重配置重算贡献度；
            保存后数据集进入「待处理」，落定前不能确认结论。
          </p>
          <ul className="plain-list">
            {detail.待选模型.map((model) => (
              <li key={String(model.id)}>
                <span>{text(model['模型编号'])} · {text(model['模型名称'])}</span>
                <span className="muted">直接 {text(model['直接贡献率'])}% / 间接 {text(model['间接贡献率'])}%</span>
                <button
                  className="link"
                  type="button"
                  disabled={busy}
                  onClick={() => void act('保存归因明细数据集', { 归因模型编号: model['模型编号'] })}
                >
                  保存数据集
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="section-title">落库凭证追溯</h3>
      <table className="data-table">
        <thead>
          <tr><th>凭证编号</th><th>数据编号</th><th>动作</th><th>内容摘要</th><th>操作人</th><th>落库时间</th></tr>
        </thead>
        <tbody>
          {detail.落库凭证.map((receipt, index) => (
            <tr key={`${text(receipt['凭证编号'])}-${index}`}>
              <td>{text(receipt['凭证编号'])}</td>
              <td>{text(receipt['数据编号'])}</td>
              <td>{text(receipt['动作'])}</td>
              <td>{text(receipt['内容摘要'])}</td>
              <td>{text(receipt['操作人'])}</td>
              <td>{text(receipt['落库时间'])}</td>
            </tr>
          ))}
          {detail.落库凭证.length === 0 && (
            <tr><td className="empty-state" colSpan={6}>暂无落库凭证</td></tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>{ruleSummary(detail.口径)}</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      <div className="page-actions detail-actions">
        <button
          className="btn primary"
          type="button"
          onClick={() => navigate('/metricdrill')}
        >
          执行结束，返回指标归因
        </button>
      </div>
    </section>
  )
}

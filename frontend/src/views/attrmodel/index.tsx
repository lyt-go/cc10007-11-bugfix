import { useCallback, useEffect, useMemo, useState } from 'react'

import { postAction, request } from '@/api/client'

const ENDPOINT = '/api/attrmodel'
const columns = [
  "模型编号",
  "模型名称",
  "归因逻辑",
  "触点权重分布",
  "衰减天数",
  "转化窗口",
  "直接贡献率",
  "间接贡献率"
]
// 评审中不能直接发布：先「评审通过」拿到通过的评审结论，再「发布模型」
const actions = [
  "提交模型评审",
  "评审通过",
  "发布模型",
  "下线模型",
  "重新发布模型"
]
// 每个动作允许的起始状态，避免把后端会拒绝的按钮摆在页面上
const actionSources: Record<string, string[]> = {
  "提交模型评审": ["草稿", "已下线"],
  "评审通过": ["模型评审中"],
  "发布模型": ["模型评审中"],
  "下线模型": ["已发布"],
  "重新发布模型": ["已下线"]
}
const statuses = [
  "草稿",
  "模型评审中",
  "已发布",
  "已下线"
]

type Row = Record<string, string | number | null | Record<string, number> | null>

type Receipt = {
  回执编号: string
  归因模型编号: string
  下线时间: string
  替代模型编号: string | null
  影响指标分析: string[]
  传播摘要: string
}

export default function AttrmodelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const stats = useMemo(() => {
    const published = rows.filter((row) => row.status === '已发布').length
    const reviewing = rows.filter((row) => row.status === '模型评审中').length
    return [
      { label: '归因模型总数', value: total },
      { label: '已发布模型', value: published },
      { label: '评审中模型', value: reviewing },
    ]
  }, [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('归因模型列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      const receiptResponse = await request(`${ENDPOINT}/receipts`)
      if (receiptResponse.ok) {
        const receiptPayload = await receiptResponse.json()
        setReceipts(receiptPayload.items ?? [])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { action })
      setNotice(result.message)
      if (!result.ok) {
        setErrorMessage(result.message)
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模操作失败')
    }
  }

  function renderCell(column: string, row: Row): string {
    const value = row[column]
    if (value && typeof value === 'object') {
      return Object.entries(value as Record<string, number>)
        .map(([name, weight]) => `${name} ${weight}`)
        .join(' / ')
    }
    return value === null || value === undefined ? '—' : String(value)
  }

  return (
    <section className="page" data-module="attrmodel">
      <header className="page-head">
        <div>
          <h2>归因建模管理</h2>
          <p className="page-desc">管理多渠道归因建模，评估各触点的直接和间接贡献；模型下线会同步到指标下钻清单。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('归因模型登记入口尚未接入审批流')}>
            登记归因模型
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出归因建模清单
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
            <th>状态</th>
            <th>评审结论</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const status = String(row.status ?? '')
            const allowed = actions.filter((action) => (actionSources[action] ?? []).includes(status))
            return (
              <tr key={String(row.id)} data-status={status}>
                {columns.map((column) => <td key={column}>{renderCell(column, row)}</td>)}
                <td>{status || '—'}</td>
                <td>{String(row['评审结论'] ?? '—')}</td>
                <td className="row-actions">
                  {allowed.length === 0 && <span>—</span>}
                  {allowed.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 3}>暂无归因建模数据，可先登记归因模型</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">模型下线回执</h3>
      <table className="data-table">
        <thead>
          <tr><th>回执编号</th><th>归因模型</th><th>下线时间</th><th>替代模型</th><th>影响指标分析</th><th>传播摘要</th></tr>
        </thead>
        <tbody>
          {receipts.map((receipt) => (
            <tr key={receipt.回执编号}>
              <td>{receipt.回执编号}</td>
              <td>{receipt.归因模型编号}</td>
              <td>{receipt.下线时间}</td>
              <td>{receipt.替代模型编号 ?? '—'}</td>
              <td>{receipt.影响指标分析?.join('、') || '—'}</td>
              <td>{receipt.传播摘要}</td>
            </tr>
          ))}
          {receipts.length === 0 && (
            <tr><td className="empty-state" colSpan={6}>暂无下线回执</td></tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条归因建模记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}

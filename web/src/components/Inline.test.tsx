import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { InlineNode } from '../types'
import { Inline } from './Inline'

describe('Inline', () => {
  it('粗體包連結、行內碼；文字原樣顯示、不當成 HTML', () => {
    const nodes: InlineNode[] = [
      { type: 'strong', children: [{ type: 'link', href: 'https://a.example/1?a=1&b=2', children: [{ type: 'text', text: '<b>標題</b>' }] }] },
      { type: 'text', text: ' — ' },
      { type: 'code', text: 'x' },
    ]
    const { container } = render(<Inline nodes={nodes} />)
    const link = screen.getByRole('link', { name: '<b>標題</b>' })
    expect(link).toHaveAttribute('href', 'https://a.example/1?a=1&b=2') // 網址的 & 沒有被重複跳脫
    expect(link.parentElement?.tagName).toBe('STRONG')
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText('x').tagName).toBe('CODE')
  })

  it('不是 http(s) 的網址不會變成連結', () => {
    const nodes: InlineNode[] = [
      { type: 'link', href: 'javascript:alert(1)', children: [{ type: 'text', text: '點我' }] },
      { type: 'link', href: 'data:text/html,x', children: [{ type: 'text', text: '另一個' }] },
    ]
    render(<Inline nodes={nodes} />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText(/點我/)).toBeInTheDocument()
  })
})

import { Link } from 'react-router-dom'
import { Notice } from '../components/Notice'

export function NotFoundPage() {
  return (
    <Notice title="找不到頁面">
      回 <Link to="/">今日晨報</Link>，或到 <Link to="/reports">歷史晨報</Link> 找。
    </Notice>
  )
}

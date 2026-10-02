import { Link } from 'react-router-dom'
import { Notice } from '../components/Notice'

export function NotFoundPage() {
  return (
    <Notice title="找不到頁面">
      回 <Link to="/reports">歷史晨報</Link>。
    </Notice>
  )
}

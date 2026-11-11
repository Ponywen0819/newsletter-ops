import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
// 順序就是 cascade 的順序
import './styles/base.css'
import './styles/layout.css'
import './styles/report.css'
import './styles/feedback-page.css'
import './styles/report-list.css'
import './styles/auth.css'
import './styles/reader.css'
import './styles/kg.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)

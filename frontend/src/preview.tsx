// Phone preview build: the real web app with the API simulated in the page (see mock/server.ts).
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './ui/tokens.css'
import './ui/ui.css'
import './styles.css'
import './screens/screens.css'
import App from './App'
import { installPreviewServer } from './mock/server'

installPreviewServer()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <div className="preview-banner">
      <b>Laabam preview</b> · everything runs in this page with a simulated shop.
    </div>
    <App />
  </StrictMode>,
)

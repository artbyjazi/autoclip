import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { App } from './App'
import { AuthGate } from './auth'
import './index.css'
import { ApiKeys } from './pages/ApiKeys'
import { JobProgress } from './pages/JobProgress'
import { Providers } from './pages/Providers'
import { Review } from './pages/Review'
import { Settings } from './pages/Settings'
import { Videos } from './pages/Videos'

function Gated({ children }: { children: React.ReactNode }) {
  return <AuthGate>{children}</AuthGate>
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Gated><Videos /></Gated> },
      { path: 'jobs/:jobId', element: <Gated><JobProgress /></Gated> },
      { path: 'videos/:videoId/clips', element: <Gated><Review /></Gated> },
      { path: 'providers', element: <Gated><Providers /></Gated> },
      { path: 'api-keys', element: <Gated><ApiKeys /></Gated> },
      { path: 'settings', element: <Gated><Settings /></Gated> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)

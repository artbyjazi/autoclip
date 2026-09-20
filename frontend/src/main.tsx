import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, isRouteErrorResponse, Link, RouterProvider, useRouteError } from 'react-router-dom'

import { App } from './App'
import { AuthGate } from './auth'
import './index.css'
import { ApiKeys } from './pages/ApiKeys'
import { ClipEditor } from './pages/ClipEditor'
import { JobProgress } from './pages/JobProgress'
import { Providers } from './pages/Providers'
import { Review } from './pages/Review'
import { Settings } from './pages/Settings'
import { Videos } from './pages/Videos'

function Gated({ children }: { children: React.ReactNode }) {
  return <AuthGate>{children}</AuthGate>
}

function RouteError() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error ? error.message : 'The page could not be loaded.'
  return <main className="mx-auto max-w-xl px-5 pt-20 text-center"><h1 className="font-display text-4xl">Something went wrong</h1><p className="mt-4 text-sm text-ink-500">{message}</p><Link className="btn btn-primary mt-6" to="/">Back to projects</Link></main>
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Gated><Videos /></Gated> },
      { path: 'jobs/:jobId', element: <Gated><JobProgress /></Gated> },
      { path: 'videos/:videoId/clips', element: <Gated><Review /></Gated> },
      { path: 'clips/:clipId/edit', element: <Gated><ClipEditor /></Gated> },
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

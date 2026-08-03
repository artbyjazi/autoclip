import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { App } from './App'
import './index.css'
import { Make } from './pages/Make'
import { Settings } from './pages/Settings'
import { Working } from './pages/Working'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true, element: <Make /> },
      { path: 'jobs/:jobId', element: <Working /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)

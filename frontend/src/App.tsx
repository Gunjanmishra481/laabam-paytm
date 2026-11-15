import Demo from './Demo'
import OwnerApp from './OwnerApp'
import { RouterProvider } from './router'
import { SessionProvider } from './session'

export default function App() {
  const demo = window.location.pathname.replace(/\/+$/, '') === '/demo'
  return (
    <SessionProvider>
      {demo ? (
        <Demo />
      ) : (
        <RouterProvider>
          <OwnerApp />
        </RouterProvider>
      )}
    </SessionProvider>
  )
}

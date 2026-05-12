import { useState } from 'react';
import { Login } from './components/Login';
import { CallView } from './components/CallView';

export default function App() {
  const [token, setToken] = useState<string | null>(null);

  return (
    <main className="min-h-screen w-full bg-background text-gray-100 flex flex-col items-center justify-center">
      {!token ? (
        <Login onLogin={setToken} />
      ) : (
        <CallView token={token} onLogout={() => setToken(null)} />
      )}
    </main>
  );
}

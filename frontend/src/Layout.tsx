import { FormEvent, useState } from "react";
import { Link, Outlet, useNavigate } from "react-router-dom";
import { login } from "./api";

export function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("planner@lensplan.example.com");
  const [password, setPassword] = useState("planner-password");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const token = await login(email, password);
      localStorage.setItem("token", token);
      navigate("/");
    } catch {
      setError("Invalid email or password.");
    }
  }

  return (
    <div className="login-page">
      <div className="card login-card">
        <h1>LensPlan</h1>
        <p className="muted">Production and distribution planning for a lens network</p>
        <form onSubmit={submit}>
          <div style={{ marginBottom: "1rem" }}>
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </div>
          <div style={{ marginBottom: "1rem" }}>
            <label htmlFor="password">Password</label>
            <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {error && <p className="error">{error}</p>}
          <button type="submit" style={{ width: "100%" }}>
            Sign in
          </button>
        </form>
        <p className="muted" style={{ fontSize: "0.85rem", marginTop: "1rem" }}>
          Demo: planner@lensplan.example.com / planner-password
        </p>
      </div>
    </div>
  );
}

export function Layout() {
  const navigate = useNavigate();
  return (
    <>
      <nav className="nav">
        <span className="nav-brand">LensPlan</span>
        <div className="nav-links">
          <Link to="/">Scenarios</Link>
        </div>
        <button
          className="secondary"
          onClick={() => {
            localStorage.removeItem("token");
            navigate("/login");
          }}
        >
          Sign out
        </button>
      </nav>
      <div className="layout">
        <Outlet />
      </div>
    </>
  );
}

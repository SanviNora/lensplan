import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout, Login } from "./Layout";
import RunPage from "./pages/RunPage";
import ScenarioPage from "./pages/ScenarioPage";
import Scenarios from "./pages/Scenarios";

function Private({ children }: { children: JSX.Element }) {
  const token = localStorage.getItem("token");
  return token ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <Private>
              <Layout />
            </Private>
          }
        >
          <Route index element={<Scenarios />} />
          <Route path="scenarios/:id" element={<ScenarioPage />} />
          <Route path="runs/:id" element={<RunPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

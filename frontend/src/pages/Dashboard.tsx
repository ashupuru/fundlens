import { useEffect, useState } from "react";
import { getHealth } from "../api/client";

type ApiStatus = "checking" | "connected" | "unreachable";

export default function Dashboard() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    getHealth()
      .then(() => setApiStatus("connected"))
      .catch(() => setApiStatus("unreachable"));
  }, []);

  return (
    <main>
      <h1>FundLens</h1>
      <p>API status: {apiStatus}</p>
    </main>
  );
}

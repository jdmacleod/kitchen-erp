import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { errorMessage } from "../api/client";
import { useLogin, useMe } from "../api/queries";
import { Alert, Button, Card, Centered, Field } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

export function LoginPage() {
  usePageTitle("Sign in");
  const navigate = useNavigate();
  const me = useMe();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  if (me.data) {
    return <Navigate to="/" replace />;
  }

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    login.mutate(
      { email: email.trim(), password },
      { onSuccess: () => navigate("/", { replace: true }) },
    );
  };

  return (
    <Centered>
      <h1 className="mb-6 text-center text-2xl font-semibold tracking-tight">Kitchen ERP</h1>
      <Card>
        <form onSubmit={onSubmit} noValidate={false} className="flex flex-col gap-4" aria-labelledby="login-heading">
          <h2 id="login-heading" className="text-lg font-medium">
            Sign in
          </h2>
          {login.isError ? <Alert tone="error">{errorMessage(login.error)}</Alert> : null}
          <Field
            id="email"
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Field
            id="password"
            label="Password"
            type="password"
            name="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" disabled={login.isPending} className="w-full">
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </Centered>
  );
}

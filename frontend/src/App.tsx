import { Navigate, Route, Routes } from "react-router";
import { AuthBridge } from "./auth/AuthBridge";
import { RequireAdmin } from "./auth/RequireAdmin";
import { RequireAuth } from "./auth/RequireAuth";
import { AppShell } from "./components/AppShell";
import { CatalogPage, catalogPages } from "./pages/CatalogPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { TokensPage } from "./pages/settings/TokensPage";
import { UsersPage } from "./pages/settings/UsersPage";

/** Route table. Mounted inside a router and a QueryClientProvider by main.tsx and the tests. */
export function App() {
  return (
    <>
      <AuthBridge />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<Navigate to="/ingredients" replace />} />
            <Route path="/ingredients" element={<CatalogPage {...catalogPages.ingredients} />} />
            <Route path="/products" element={<CatalogPage {...catalogPages.products} />} />
            <Route path="/vendors" element={<CatalogPage {...catalogPages.vendors} />} />
            <Route path="/map" element={<CatalogPage {...catalogPages.map} />} />
            <Route path="/purchases" element={<CatalogPage {...catalogPages.purchases} />} />
            <Route
              path="/settings/users"
              element={
                <RequireAdmin>
                  <UsersPage />
                </RequireAdmin>
              }
            />
            <Route path="/settings/tokens" element={<TokensPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </>
  );
}

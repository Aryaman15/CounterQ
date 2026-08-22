import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Home from "../app/page";

describe("Home", () => {
  it("renders the Stage 0 foundation shell", () => {
    render(<Home />);

    expect(screen.getByRole("heading", { name: /CounterQ repository foundation/i })).toBeInTheDocument();
  });
});


import { useState } from "react";
import { formatPack, productTitle, useProductSearch, type SearchHit } from "../../api/catalog";
import { useNameWords } from "../../api/naming";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Combobox } from "../catalog/Combobox";
import { hintClass } from "../catalog/fields";

type Option =
  | { kind: "product"; hit: SearchHit }
  | { kind: "word"; word: string; learned: boolean }
  | { kind: "ingredient"; name: string };

const GROUP: Record<Option["kind"], string> = {
  product: "Products you have",
  word: "Finish the word",
  ingredient: "The ingredient's name",
};

/** The word being typed: the letters after the last space, or "" after a space. */
function lastWord(text: string): string {
  return /(\p{L}+)$/u.exec(text)?.[1] ?? "";
}

function sentence(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

/**
 * A naming row's Name, with suggestions while typing (issue 385): products the
 * catalog has (choosing one uses it), words completing the one being typed, and
 * the chosen ingredient's name. Free text is always allowed, and nothing is
 * looked up until the person types in this field.
 */
export function NameField({
  id,
  vendorId,
  value,
  ingredientName,
  hint,
  disabled,
  onChange,
  onUse,
}: {
  id: string;
  vendorId: string;
  value: string;
  ingredientName: string | null;
  hint?: string;
  disabled: boolean;
  onChange: (name: string) => void;
  onUse: (product: SearchHit) => void;
}) {
  // A prefilled name isn't a question: 300 rows mustn't search on load.
  const [typed, setTyped] = useState(false);
  const query = useDebouncedValue(typed ? value : "", 250);
  const products = useProductSearch(query.trim().length >= 3 ? query : "", 5);
  const words = useNameWords(vendorId, typed ? lastWord(query) : "");

  const typing = lastWord(value).toLowerCase();
  const options: Option[] = [
    ...(products.data ?? []).map((hit): Option => ({ kind: "product", hit })),
    ...(words.data ?? [])
      .filter((w) => typing.length > 0 && w.word.startsWith(typing))
      .map((w): Option => ({ kind: "word", word: w.word, learned: w.source === "learned" })),
    ...(ingredientName && value.trim().toLowerCase() !== ingredientName.toLowerCase()
      ? [{ kind: "ingredient", name: ingredientName } as const]
      : []),
  ];

  const choose = (option: Option) => {
    if (option.kind === "product") onUse(option.hit);
    else if (option.kind === "word")
      onChange(sentence(value.slice(0, value.length - lastWord(value).length) + option.word));
    else onChange(sentence(option.name));
  };

  return (
    <Combobox<Option>
      id={id}
      label="Name"
      inputValue={value}
      onInputChange={(next) => {
        setTyped(true);
        onChange(next);
      }}
      items={typed ? options : []}
      getKey={(o) =>
        o.kind === "product" ? `p:${o.hit.id}` : o.kind === "word" ? `w:${o.word}` : "i"
      }
      groupOf={(o) => GROUP[o.kind]}
      renderItem={(o) =>
        o.kind === "product" ? (
          <span>
            Use {productTitle(o.hit)}
            {o.hit.pack_qty ? (
              <span className={hintClass}> · {formatPack(o.hit.pack_qty, o.hit.pack_unit)}</span>
            ) : null}
          </span>
        ) : o.kind === "word" ? (
          <span>
            {o.word}
            {o.learned ? <span className={hintClass}> · learned at this store</span> : null}
          </span>
        ) : (
          <span>{sentence(o.name)}</span>
        )
      }
      getLabel={(o) =>
        o.kind === "product"
          ? `Use the existing product ${productTitle(o.hit)}`
          : o.kind === "word"
            ? `Finish the word as ${o.word}`
            : `Name it ${sentence(o.name)}`
      }
      onSelect={choose}
      listLabel="Name suggestions"
      hint={hint}
      disabled={disabled}
    />
  );
}

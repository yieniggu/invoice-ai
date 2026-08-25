import argparse

from invoiceops.ml.data import generate_synthetic_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic synthetic invoice dataset."
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        output_dir = generate_synthetic_dataset(
            seed=args.seed, rows=args.rows, version=args.version
        )
    except ValueError as error:
        parser.error(str(error))
    print(output_dir)


if __name__ == "__main__":
    main()

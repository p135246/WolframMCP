calculateSum[a_, b_] :=
  a + b

square[x_] :=
  x^2

main[] :=
  Module[{result},
    result = calculateSum[square[3], square[4]];
    Print[result]
  ]

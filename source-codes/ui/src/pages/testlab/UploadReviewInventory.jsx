import { forwardRef } from "react";
import VariableInventory from "./VariableInventory";

const UploadReviewInventory = forwardRef(function UploadReviewInventory(props, ref) {
  return <VariableInventory ref={ref} {...props} />;
});

export default UploadReviewInventory;

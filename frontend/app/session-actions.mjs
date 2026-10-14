/** A failed network request must not be reported as a completed logout. */
export async function endSession(api){
 try{await api('/auth/logout','POST',undefined,15000)}catch(error){if(error?.status!==401)throw error}
}

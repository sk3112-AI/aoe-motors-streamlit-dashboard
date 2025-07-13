import { serve } from "https://deno.land/std@0.177.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.38.5";

// Initialize Supabase client within the Edge Function
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are automatically available as env vars in Edge Functions
const supabase = createClient(
  Deno.env.get('SUPABASE_URL') ?? '',
  Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '',
  {
    auth: {
      persistSession: false
    }
  }
);

console.log(`Function "track-email-event" up and running!`);

serve(async (req) => {
  const url = new URL(req.url);
  const request_id = url.searchParams.get('request_id'); // Get lead ID
  const event_type = url.searchParams.get('event_type'); // e.g., 'opened', 'clicked_video', 'clicked_pdf'
  const redirect_to = url.searchParams.get('redirect_to'); // URL to redirect to for link clicks

  console.log(`Tracking event: request_id=${request_id}, event_type=${event_type}, redirect_to=${redirect_to}`);

  // Basic validation
  if (!request_id || !event_type) {
    console.error('Missing required parameters: request_id or event_type');
    return new Response('Missing required parameters', { status: 400 });
  }

  try {
    // --- 1. Fetch current lead score from 'bookings' table ---
    const { data: bookingData, error: fetchError } = await supabase
      .from('bookings')
      .select('numeric_lead_score, lead_score')
      .eq('request_id', request_id)
      .single();

    if (fetchError) {
      console.error(`Supabase fetch error for request_id ${request_id}:`, fetchError);
      // Continue to insert interaction even if score fetch fails, but log it.
    }

    let currentNumericScore = bookingData ? bookingData.numeric_lead_score || 0 : 0;
    let newNumericScore = currentNumericScore;

    // --- 2. Calculate points for the event type ---
    let pointsEarned = 0;

    if (event_type === 'opened') {
      // Check if an 'opened' event has ALREADY been recorded for this request_id
      const { count: openedCount, error: countError } = await supabase
        .from('email_interactions')
        .select('id', { count: 'exact' })
        .eq('request_id', request_id)
        .eq('event_type', 'opened');
      
      if (countError) {
        console.error(`Supabase count error for opened event:`, countError);
        // Default to adding points if count check fails to avoid blocking.
        pointsEarned = 1; 
      } else if (openedCount === 0) {
        // Only add points if this is the FIRST 'opened' event for this request_id
        pointsEarned = 1;
        console.log(`First 'opened' event for ${request_id}. Adding 1 point.`);
      } else {
        // 'opened' event already exists, so no additional points
        pointsEarned = 0;
        console.log(`'opened' event already recorded for ${request_id}. No points added.`);
      }
    } else if (event_type === 'clicked_video') {
      pointsEarned = 2;
    } else if (event_type === 'clicked_pdf') {
      pointsEarned = 2;
    } else {
      console.warn(`Unknown event_type: ${event_type}. No points added.`);
    }

    newNumericScore = currentNumericScore + pointsEarned;
    // --- 3. Cap the score at 15 ---
    newNumericScore = Math.min(newNumericScore, 15);

    // --- 4. Determine new text lead_score based on new numeric score ---
    let newLeadScoreText: string;
    if (newNumericScore >= 10) {
      newLeadScoreText = "Hot";
    } else if (newNumericScore >= 5) {
      newLeadScoreText = "Warm";
    } else {
      newLeadScoreText = "Cold";
    }

    // --- 5. Update 'bookings' table with new scores ---
    if (bookingData) { // Only update if a booking was found
        const { error: updateError } = await supabase
        .from('bookings')
        .update({ 
            numeric_lead_score: newNumericScore,
            lead_score: newLeadScoreText // Update the text score as well
        })
        .eq('request_id', request_id);

        if (updateError) {
            console.error(`Supabase update error for request_id ${request_id}:`, updateError);
        } else {
            console.log(`Updated lead scores for ${request_id}: numeric=${newNumericScore}, status=${newLeadScoreText}`);
        }
    } else {
        console.warn(`Booking with request_id ${request_id} not found. Cannot update lead score.`);
    }

    // --- 6. Insert interaction event into Supabase ('email_interactions') ---
    const { data: insertData, error: insertError } = await supabase
      .from('email_interactions') // THIS TABLE NEEDS TO EXIST IN SUPABASE
      .insert({
        request_id: request_id,
        event_type: event_type
      }).select(); // Selects the inserted row

    if (insertError) {
      console.error(`Supabase insert error for email_interactions:`, insertError);
      // Note: We don't return an error here, as the primary goal is often to redirect the user
      // and silently logging the DB issue might be preferable to breaking the user experience.
    } else {
      console.log(`Successfully logged event: ${event_type} for ${request_id} into email_interactions.`);
    }

    // --- Handle redirection or 204 response ---
    if (event_type.startsWith('clicked_') && redirect_to) {
      try {
        const decodedRedirectUrl = decodeURIComponent(redirect_to);
        console.log(`Redirecting to: ${decodedRedirectUrl}`);
        return Response.redirect(decodedRedirectUrl, 302); // HTTP 302 Found for redirection
      } catch (decodeError) {
        console.error(`Error decoding redirect_to URL:`, decodeError);
        return new Response('Invalid redirect URL', { status: 400 });
      }
    } else {
      // For email open pixels, return a tiny transparent GIF (or 204 No Content)
      return new Response(null, { status: 204 }); // 204 No Content
    }
  } catch (err) {
    console.error(`Caught unexpected error in track-email-event:`, err);
    return new Response(`Error: ${err.message}`, { status: 500 });
  }
});
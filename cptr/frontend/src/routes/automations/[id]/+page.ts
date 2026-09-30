/** `/automations/<id>` → `/scheduled/<id>`, where the board opens that row. */
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
	redirect(308, `/scheduled/${params.id}`);
};
